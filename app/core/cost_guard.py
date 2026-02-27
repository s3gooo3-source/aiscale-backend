"""
Cost guardrails:
- Tracks daily spend from usage_daily
- Exposes a kill switch: if today's cost > MAX_DAILY_COST_USD, block AI calls
- Computes token cost
- Uses a 60-second cache on the daily cost check (avoid DB hammering)
"""
import time
import hashlib
import logging
from datetime import date
from typing import Optional
from fastapi import HTTPException

from app.core.config import settings
from app.core.clients import db

logger = logging.getLogger(__name__)

# ── In-process cache for daily cost check ────────────────────
_cost_cache: dict = {"value": 0.0, "fetched_at": 0.0, "date": None}
_CACHE_TTL_SECS = 60  # refresh every 60 seconds


async def get_today_total_cost() -> float:
    """Return today's total estimated AI cost across ALL users (for kill switch)."""
    now = time.time()
    today = date.today()

    if (
        _cost_cache["date"] == today
        and now - _cost_cache["fetched_at"] < _CACHE_TTL_SECS
    ):
        return _cost_cache["value"]

    try:
        result = db.table("usage_daily").select(
            "estimated_cost_usd"
        ).eq("date", today.isoformat()).execute()

        total = sum(float(r.get("estimated_cost_usd") or 0) for r in (result.data or []))
        _cost_cache.update({"value": total, "fetched_at": now, "date": today})
        return total
    except Exception as e:
        logger.error(f"cost_guard: failed to fetch daily cost: {e}")
        return _cost_cache["value"]  # return stale rather than crashing


async def enforce_kill_switch() -> None:
    """Raise 503 if today's total AI cost exceeds MAX_DAILY_COST_USD."""
    today_cost = await get_today_total_cost()
    if today_cost >= settings.MAX_DAILY_COST_USD:
        logger.warning(
            f"KILL SWITCH: daily cost ${today_cost:.4f} >= threshold ${settings.MAX_DAILY_COST_USD}"
        )
        raise HTTPException(
            status_code=503,
            detail={
                "service_paused": True,
                "message": (
                    "AI temporarily paused for cost protection. "
                    "Normal service resumes at midnight UTC."
                ),
                "daily_cost_usd": round(today_cost, 4),
                "threshold_usd": settings.MAX_DAILY_COST_USD,
            },
        )


def estimate_cost(input_tokens: int, output_tokens: int) -> float:
    """Return estimated USD cost for a single OpenAI call."""
    return (
        input_tokens  * settings.COST_PER_INPUT_TOKEN
        + output_tokens * settings.COST_PER_OUTPUT_TOKEN
    )


def record_cost(cost: float) -> None:
    """Immediately update the in-process cache so kill switch reacts fast."""
    _cost_cache["value"] = _cost_cache.get("value", 0.0) + cost


# ── FAQ cache helpers ─────────────────────────────────────────
def hash_question(text: str) -> str:
    """Normalise and hash a question for cache lookup."""
    normalised = " ".join(text.lower().strip().split())
    return hashlib.sha256(normalised.encode()).hexdigest()


async def get_cached_answer(owner_id: str, question: str) -> Optional[str]:
    """Return cached answer if it exists and hasn't expired, else None."""
    h = hash_question(question)
    try:
        result = db.table("faq_cache").select("answer_text").eq(
            "owner_id", owner_id
        ).eq("question_hash", h).gt(
            "expires_at", "now()"
        ).execute()

        row = (result.data or [None])[0]
        if row:
            # Bump hit count asynchronously (fire-and-forget style)
            db.table("faq_cache").update(
                {"hit_count": db.raw("hit_count + 1")}
            ).eq("owner_id", owner_id).eq("question_hash", h).execute()
            return row["answer_text"]
    except Exception as e:
        logger.warning(f"FAQ cache lookup failed: {e}")
    return None


async def store_cached_answer(
    owner_id: str,
    question: str,
    answer: str,
    model: str,
) -> None:
    """Store a new FAQ cache entry with TTL."""
    h = hash_question(question)
    try:
        db.table("faq_cache").upsert(
            {
                "owner_id":      owner_id,
                "question_hash": h,
                "question_text": question[:500],
                "answer_text":   answer,
                "model_used":    model,
                "hit_count":     1,
                "expires_at":    f"now() + interval '{settings.FAQ_CACHE_TTL_HOURS} hours'",
            },
            on_conflict="owner_id,question_hash",
        ).execute()
    except Exception as e:
        logger.warning(f"FAQ cache store failed: {e}")
