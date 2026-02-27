"""
/api/demo/chat — Public endpoint.

Security layers (in order):
1. Kill switch (daily cost threshold — includes demo cost)
2. IP rate limit  (5/min sliding window)
3. IP daily limit (20/day via demo_sessions)
4. Session limit  (10 messages/session)
5. Input validation (max chars, Pydantic)
6. FAQ cache check (skip OpenAI if cached)
7. OpenAI call (gpt-4o-mini, max 500 output tokens)
8. Cost tracked: increment_usage() under DEMO system user
9. Conversation + message logged to DB for full audit trail

Part 2: Demo cost persistence — ALL demo OpenAI calls are logged
under the system profile 00000000-0000-0000-0000-000000000000
and system store 00000000-0000-0000-0000-000000000001.
This means the kill-switch aggregation sees 100% of spend.
"""
import secrets
import logging
from datetime import datetime, timezone, date
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, validator
from typing import Optional, List

from app.core.config import settings
from app.core.clients import db, openai_client
from app.core.rate_limit import rate_limiter
from app.core.cost_guard import (
    enforce_kill_switch,
    estimate_cost,
    record_cost,
    get_cached_answer,
    store_cached_answer,
)
from app.core.ownership import record_usage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/demo")

# System identifiers — match SQL inserts in schema v3
DEMO_OWNER_ID = "00000000-0000-0000-0000-000000000000"
DEMO_STORE_ID = "00000000-0000-0000-0000-000000000001"

SYSTEM_PROMPT = """You are Alex, the AI support assistant for "Bloom Boutique" — a demo Shopify fashion store.

IMPORTANT: Always tell users this is a demo using sample store data (not a real Shopify store).

Store info:
- Products: Women's clothing, accessories, shoes. $25–$350.
- Returns: 30 days from delivery. Free return shipping on defective/wrong items. Sale items = store credit only.
- Shipping: Free over $75. Standard 3-5 days. Express $12.99.

Demo orders you can reference:
- #45892 → Blue Wrap Dress, UPS, out for delivery today
- #38821 → Leather Tote Bag, delivered 3 days ago
- #12345 → White Linen Blouse, processing, not yet shipped

Rules:
- Concise replies (2-4 sentences). One emoji max per reply.
- Never guess. If you don't know, say so.
- If customer is upset or requests a human: say you are escalating to the team.
- If asked "are you AI?" — confirm you are."""


class ChatRequest(BaseModel):
    message: str
    session_token: Optional[str] = None
    history: List[dict] = []

    @validator("message")
    def check_message(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("Message cannot be empty")
        if len(v) > settings.MAX_INPUT_CHARS:
            raise ValueError(f"Message too long (max {settings.MAX_INPUT_CHARS} characters)")
        return v

    @validator("history")
    def check_history(cls, v):
        return v[-10:] if len(v) > 10 else v


async def _get_or_create_session(ip: str, session_token: Optional[str]) -> str:
    """Validate session; create new one if needed."""
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    # IP daily cap
    ip_rows = db.table("demo_sessions").select("message_count").eq(
        "ip_address", ip
    ).gte("created_at", today_start.isoformat()).execute()
    ip_total = sum(r.get("message_count", 0) for r in (ip_rows.data or []))
    if ip_total >= settings.DEMO_RATE_LIMIT_PER_IP:
        raise HTTPException(
            429,
            detail={
                "error": "daily_limit",
                "message": "Daily demo limit reached. Create a free account for more.",
            },
        )

    # Session lookup or create
    if session_token:
        row = db.table("demo_sessions").select("*").eq(
            "session_token", session_token
        ).execute()
        session = (row.data or [None])[0]
        if session:
            if session["message_count"] >= settings.DEMO_RATE_LIMIT_PER_SESSION:
                raise HTTPException(
                    429,
                    detail={
                        "error": "session_limit",
                        "message": (
                            f"Demo session limit reached "
                            f"({settings.DEMO_RATE_LIMIT_PER_SESSION} messages). "
                            "Sign up free for unlimited access."
                        ),
                    },
                )
            return session_token

    new_token = secrets.token_urlsafe(24)
    db.table("demo_sessions").insert(
        {"session_token": new_token, "ip_address": ip, "message_count": 0}
    ).execute()
    return new_token


async def _log_demo_conversation(
    session_token: str,
    user_message: str,
    ai_reply: str,
    model: str,
    in_tokens: int,
    out_tokens: int,
    cost: float,
    cache_hit: bool,
) -> None:
    """
    Log demo conversation + messages to DB under the system user.
    Non-fatal — never blocks the API response.
    Also calls record_usage() so kill switch aggregation is complete.
    """
    try:
        now = datetime.now(timezone.utc).isoformat()

        # Upsert a conversation per session_token
        conv_result = db.table("conversations").select("id").eq(
            "owner_id", DEMO_OWNER_ID
        ).eq("customer_name", f"demo:{session_token[:16]}").execute()

        existing = (conv_result.data or [None])[0]
        if existing:
            conv_id = existing["id"]
        else:
            ins = db.table("conversations").insert({
                "owner_id":        DEMO_OWNER_ID,
                "store_id":        DEMO_STORE_ID,
                "customer_name":   f"demo:{session_token[:16]}",
                "status":          "active",
                "last_message_at": now,
            }).execute()
            conv_id = ins.data[0]["id"]

        # Log user message
        db.table("messages").insert({
            "conversation_id": conv_id,
            "owner_id":        DEMO_OWNER_ID,
            "role":            "user",
            "content":         user_message[:2000],
            "model_used":      None,
            "input_tokens":    0,
            "output_tokens":   0,
            "cache_hit":       False,
        }).execute()

        # Log assistant reply
        db.table("messages").insert({
            "conversation_id": conv_id,
            "owner_id":        DEMO_OWNER_ID,
            "role":            "assistant",
            "content":         ai_reply[:4000],
            "model_used":      model if not cache_hit else "cache",
            "input_tokens":    in_tokens,
            "output_tokens":   out_tokens,
            "cache_hit":       cache_hit,
        }).execute()

        # Update conversation aggregates
        db.table("conversations").update({
            "message_count":  db.raw("message_count + 2"),
            "input_tokens":   db.raw(f"input_tokens + {in_tokens}"),
            "output_tokens":  db.raw(f"output_tokens + {out_tokens}"),
            "ai_cost_usd":    db.raw(f"ai_cost_usd + {cost}"),
            "last_message_at": now,
        }).eq("id", conv_id).execute()

        # Persist to usage_daily — this is what the kill switch reads
        if not cache_hit:
            await record_usage(DEMO_OWNER_ID, DEMO_STORE_ID, in_tokens, out_tokens, cost)

    except Exception as e:
        logger.warning(f"demo conversation log failed (non-fatal): {e}")


@router.post("/chat")
async def demo_chat(request: Request, body: ChatRequest):
    # Client IP
    client_ip = (
        request.headers.get("x-forwarded-for", request.client.host or "unknown")
        .split(",")[0]
        .strip()
    )

    # 1. Kill switch (includes demo cost via system user)
    await enforce_kill_switch()

    # 2. IP rate limit
    await rate_limiter.check(
        f"ip:{client_ip}",
        limit=settings.IP_RATE_LIMIT_PER_MINUTE,
        window_secs=60,
    )

    # 3. Session management
    session_token = await _get_or_create_session(client_ip, body.session_token)

    # 4. FAQ cache
    cached = await get_cached_answer(DEMO_OWNER_ID, body.message)
    if cached:
        db.table("demo_sessions").update(
            {"message_count": db.raw("message_count + 1")}
        ).eq("session_token", session_token).execute()
        # Still log cache hits for audit trail (tokens=0, cost=0)
        await _log_demo_conversation(
            session_token, body.message, cached, "cache", 0, 0, 0.0, True
        )
        return {
            "reply":         cached,
            "session_token": session_token,
            "model":         "cache",
            "tokens":        0,
            "cache_hit":     True,
            "notice":        "Demo uses sample Shopify store data — not a real store.",
        }

    # 5. Build messages
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in body.history:
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and m.get("content"):
            messages.append({"role": m["role"], "content": str(m["content"])[:500]})
    messages.append({"role": "user", "content": body.message})

    # 6. OpenAI call
    try:
        response = await openai_client.chat.completions.create(
            model=settings.DEMO_MODEL,
            messages=messages,
            max_tokens=settings.MAX_OUTPUT_TOKENS,
            temperature=0.7,
        )
        reply      = response.choices[0].message.content
        in_tokens  = response.usage.prompt_tokens     if response.usage else 0
        out_tokens = response.usage.completion_tokens if response.usage else 0
    except Exception as e:
        logger.error(f"OpenAI error in demo: {e}")
        raise HTTPException(503, "AI service error. Please try again.")

    # 7. Cost tracking — update in-process cache immediately
    cost = estimate_cost(in_tokens, out_tokens)
    record_cost(cost)

    # 8. FAQ cache store
    await store_cached_answer(DEMO_OWNER_ID, body.message, reply, settings.DEMO_MODEL)

    # 9. Increment session count
    db.table("demo_sessions").update(
        {"message_count": db.raw("message_count + 1")}
    ).eq("session_token", session_token).execute()

    # 10. Full DB persistence (conversations + messages + usage_daily)
    await _log_demo_conversation(
        session_token, body.message, reply,
        settings.DEMO_MODEL, in_tokens, out_tokens, cost, False
    )

    return {
        "reply":         reply,
        "session_token": session_token,
        "model":         settings.DEMO_MODEL,
        "tokens":        in_tokens + out_tokens,
        "cache_hit":     False,
        "notice":        "Demo uses sample Shopify store data — not a real store.",
    }
