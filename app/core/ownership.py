"""
Ownership validation helpers.

The backend NEVER trusts store_id, conversation_id, etc. from the client.
All ownership is verified against owner_id = current_user before use.
"""
import logging
from fastapi import HTTPException
from app.core.clients import db

logger = logging.getLogger(__name__)


def _not_found(resource: str, rid: str):
    """Standard 404 — deliberately vague to avoid resource enumeration."""
    raise HTTPException(
        status_code=404,
        detail=f"{resource} not found.",
    )


async def get_owned_store(owner_id: str) -> dict:
    """
    Fetch the store that belongs to this user.
    Raises 404 if none exists — never returns another user's store.
    """
    try:
        result = db.table("stores").select("*").eq(
            "owner_id", owner_id
        ).execute()
        store = (result.data or [None])[0]
    except Exception as e:
        logger.error(f"get_owned_store error: {e}")
        raise HTTPException(500, "Database error")

    if not store:
        raise HTTPException(404, "No store found. Complete onboarding first.")
    return store


async def get_owned_store_or_none(owner_id: str) -> dict | None:
    """Like get_owned_store but returns None instead of raising."""
    try:
        result = db.table("stores").select("*").eq("owner_id", owner_id).execute()
        return (result.data or [None])[0]
    except Exception:
        return None


async def verify_conversation_ownership(conversation_id: str, owner_id: str) -> dict:
    """
    Verify a conversation_id belongs to this owner.
    Raises 404 (not 403) to prevent leaking existence of other users' data.
    """
    try:
        result = db.table("conversations").select("*").eq(
            "id", conversation_id
        ).eq(
            "owner_id", owner_id  # ← double-check: id AND owner
        ).execute()
        conv = (result.data or [None])[0]
    except Exception as e:
        logger.error(f"verify_conversation error: {e}")
        raise HTTPException(500, "Database error")

    if not conv:
        _not_found("Conversation", conversation_id)
    return conv


async def check_usage_limits(owner_id: str, store: dict) -> None:
    """
    Check if the user has exceeded their plan's monthly limits.
    Raises HTTP 402 with structured payload if exceeded.

    Checks:
    1. Monthly conversation count
    2. Monthly token count
    """
    from datetime import date

    plan       = store.get("plan_type", "free_beta")
    conv_limit = store.get("monthly_limit_conversations", 100)
    tok_limit  = store.get("monthly_limit_tokens", 50_000)

    # Get this month's totals from usage_daily
    month_start = date.today().replace(day=1).isoformat()
    try:
        result = db.table("usage_daily").select(
            "conversations, total_tokens"
        ).eq("owner_id", owner_id).gte("date", month_start).execute()

        rows = result.data or []
        total_convs   = sum(r.get("conversations", 0) for r in rows)
        total_tokens  = sum(r.get("total_tokens", 0) for r in rows)
    except Exception as e:
        logger.error(f"check_usage_limits error: {e}")
        return  # fail open: don't block user on DB error

    if total_convs >= conv_limit:
        raise HTTPException(
            status_code=402,
            detail={
                "limit_reached":    True,
                "limit_type":       "conversations",
                "message":          "Monthly conversation limit reached. Upgrade to continue.",
                "current":          total_convs,
                "limit":            conv_limit,
                "plan":             plan,
            },
        )

    if total_tokens >= tok_limit:
        raise HTTPException(
            status_code=402,
            detail={
                "limit_reached":    True,
                "limit_type":       "tokens",
                "message":          "Monthly token limit reached. Upgrade to continue.",
                "current":          total_tokens,
                "limit":            tok_limit,
                "plan":             plan,
            },
        )


async def record_usage(
    owner_id: str,
    store_id: str | None,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
) -> None:
    """Atomically increment daily usage via the DB helper function."""
    try:
        db.rpc("increment_usage", {
            "p_owner_id":      owner_id,
            "p_store_id":      store_id,
            "p_input_tokens":  input_tokens,
            "p_output_tokens": output_tokens,
            "p_cost_usd":      cost_usd,
        }).execute()
    except Exception as e:
        logger.error(f"record_usage failed (non-fatal): {e}")
        # Non-fatal: usage tracking failure must not break the user experience
