"""
/api/chat — Authenticated AI chat endpoint.

Security layers (in order):
1.  Auth required (JWT verified server-side)
2.  Kill switch  (daily cost threshold — includes demo cost)
3.  Per-user rate limit (10/min sliding window)
4.  Ownership: store fetched by owner_id ONLY — never client-supplied
5.  Plan usage limits checked BEFORE OpenAI call (raises 402)
6.  Input validation (Pydantic, max chars)
7.  FAQ cache check (24h dedup — skips OpenAI if hit)
8.  OpenAI call (gpt-4o-mini, max 500 output tokens)
9.  Cost tracked in-process + persisted to usage_daily
10. Response carries X-Request-ID (from logging middleware)
    and x-cost-usd header (read by logging middleware for structured logs)

Part 3: Uses get_request_id() so every log line is correlated.
Part 5: Sets x-cost-usd header for per-request cost logging.
"""
import logging
from fastapi import APIRouter, Request, HTTPException, Depends, Response
from pydantic import BaseModel, validator
from typing import Optional, List

from app.core.config import settings
from app.core.clients import openai_client, db
from app.core.auth import get_user
from app.core.rate_limit import rate_limiter
from app.core.cost_guard import (
    enforce_kill_switch,
    estimate_cost,
    record_cost,
    get_cached_answer,
    store_cached_answer,
)
from app.core.ownership import (
    get_owned_store,
    check_usage_limits,
    record_usage,
)
from app.api.middleware.logging_middleware import get_request_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat")


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    history: List[dict] = []

    @validator("message")
    def check_message(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("Empty message")
        if len(v) > settings.MAX_INPUT_CHARS:
            raise ValueError(f"Message too long (max {settings.MAX_INPUT_CHARS} chars)")
        return v

    @validator("history")
    def check_history(cls, v):
        return v[-10:] if len(v) > 10 else v


def _build_system_prompt(store: dict, store_settings: dict | None) -> str:
    bot_name  = (store_settings or {}).get("bot_name",  "Alex")
    bot_tone  = (store_settings or {}).get("bot_tone",  "friendly")
    custom    = (store_settings or {}).get("custom_instructions", "") or ""
    shop_name = store.get("shop_name") or store.get("shopify_domain") or "the store"

    tone_map = {
        "friendly": "warm, helpful, and professional",
        "formal":   "formal and concise",
        "casual":   "casual and friendly",
        "neutral":  "neutral and factual",
    }
    tone_desc = tone_map.get(bot_tone, "helpful and professional")

    prompt = (
        f"You are {bot_name}, the AI customer support assistant for {shop_name}. "
        f"Your communication style is {tone_desc}. "
        "Answer customer questions about orders, returns, and products. "
        "Keep replies concise (2-4 sentences). "
        "If asked whether you are AI, confirm that you are. "
        "If the customer is upset or requests a human agent, say you are creating an escalation ticket."
    )
    if custom:
        prompt += f"\n\nAdditional instructions:\n{custom}"
    return prompt


@router.post("/message")
async def authenticated_chat(
    request: Request,
    response: Response,
    body: ChatRequest,
    user: dict = Depends(get_user),
):
    owner_id   = user["user_id"]
    request_id = get_request_id()  # Part 5: correlate logs

    log_extra = {
        "request_id": request_id,
        "user_id":    owner_id,
        "endpoint":   "/api/chat/message",
    }

    # 1. Kill switch
    await enforce_kill_switch()

    # 2. Per-user rate limit
    await rate_limiter.check(
        f"user:{owner_id}",
        limit=settings.AUTH_RATE_LIMIT_PER_MINUTE,
        window_secs=60,
    )

    # 3. Fetch store — ALWAYS by owner_id, NEVER by client-supplied ID
    store    = await get_owned_store(owner_id)
    store_id = str(store["id"])
    log_extra["store_id"] = store_id

    # 4. Plan usage limit check
    await check_usage_limits(owner_id, store)

    # 5. Load bot settings (owned by this user only)
    settings_result = db.table("store_settings").select(
        "bot_name,bot_tone,custom_instructions,escalation_threshold"
    ).eq("owner_id", owner_id).execute()
    store_settings = (settings_result.data or [None])[0]

    # 6. FAQ cache check
    cached = await get_cached_answer(owner_id, body.message)
    if cached:
        logger.info("cache hit", extra={**log_extra, "cache_hit": True, "cost_usd": 0})
        return {
            "reply":           cached,
            "conversation_id": body.conversation_id,
            "model":           "cache",
            "tokens":          0,
            "cache_hit":       True,
        }

    # 7. Build messages array
    system_prompt = _build_system_prompt(store, store_settings)
    messages = [{"role": "system", "content": system_prompt}]
    for m in body.history:
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and m.get("content"):
            messages.append({"role": m["role"], "content": str(m["content"])[:500]})
    messages.append({"role": "user", "content": body.message})

    # 8. OpenAI call
    try:
        oai_response = await openai_client.chat.completions.create(
            model=settings.DEMO_MODEL,
            messages=messages,
            max_tokens=settings.MAX_OUTPUT_TOKENS,
            temperature=0.7,
        )
        reply      = oai_response.choices[0].message.content
        in_tokens  = oai_response.usage.prompt_tokens     if oai_response.usage else 0
        out_tokens = oai_response.usage.completion_tokens if oai_response.usage else 0
    except Exception as e:
        logger.error(f"OpenAI error: {e}", extra=log_extra)
        raise HTTPException(503, "AI service unavailable. Please try again.")

    # 9. Cost tracking
    cost = estimate_cost(in_tokens, out_tokens)
    record_cost(cost)
    await record_usage(owner_id, store_id, in_tokens, out_tokens, cost)

    # 10. Cache for future identical questions
    await store_cached_answer(owner_id, body.message, reply, settings.DEMO_MODEL)

    # Part 5: annotate response with cost so logging middleware can capture it
    response.headers["x-cost-usd"] = str(round(cost, 8))

    logger.info(
        "chat completed",
        extra={
            **log_extra,
            "in_tokens":  in_tokens,
            "out_tokens": out_tokens,
            "cost_usd":   round(cost, 8),
            "cache_hit":  False,
        },
    )

    return {
        "reply":           reply,
        "conversation_id": body.conversation_id,
        "model":           settings.DEMO_MODEL,
        "input_tokens":    in_tokens,
        "output_tokens":   out_tokens,
        "cache_hit":       False,
    }
