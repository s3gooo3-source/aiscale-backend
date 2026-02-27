"""
/api/dashboard — Real metrics from Supabase.

All queries enforce owner_id = current_user.
No client-supplied IDs are trusted for ownership.
Part 5: request_id wired to error logs.
"""
import logging
from datetime import datetime, timezone, timedelta, date
from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import get_user
from app.core.clients import db
from app.core.config import settings
from app.core.ownership import get_owned_store_or_none
from app.api.middleware.logging_middleware import get_request_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dashboard")


@router.get("/metrics")
async def get_metrics(user: dict = Depends(get_user)):
    owner_id   = user["user_id"]
    request_id = get_request_id()

    try:
        # Monthly usage from usage_daily
        month_start = date.today().replace(day=1).isoformat()
        usage_result = db.table("usage_daily").select(
            "date, conversations, total_tokens, estimated_cost_usd"
        ).eq("owner_id", owner_id).gte("date", month_start).order("date").execute()
        usage_rows = usage_result.data or []

        monthly_conversations = sum(r.get("conversations", 0)  for r in usage_rows)
        monthly_tokens        = sum(r.get("total_tokens", 0)   for r in usage_rows)
        monthly_cost          = sum(float(r.get("estimated_cost_usd", 0)) for r in usage_rows)

        # 7-day breakdown
        seven_ago  = (date.today() - timedelta(days=6)).isoformat()
        week_rows  = [r for r in usage_rows if r["date"] >= seven_ago]
        convs_7d   = sum(r.get("conversations", 0) for r in week_rows)

        # Conversations for automation/escalation split
        conv_result = db.table("conversations").select(
            "id, resolution_type, ai_cost_usd, created_at"
        ).eq("owner_id", owner_id).gte(
            "created_at", f"{month_start}T00:00:00Z"
        ).execute()
        conv_rows = conv_result.data or []

        automated       = sum(1 for r in conv_rows if r.get("resolution_type") == "automated")
        escalated_count = sum(1 for r in conv_rows if r.get("resolution_type") == "escalated")
        automation_rate = round(100 * automated / len(conv_rows), 1) if conv_rows else 0.0

        # Plan + limits
        store      = await get_owned_store_or_none(owner_id)
        plan_type  = (store or {}).get("plan_type", settings.DEFAULT_PLAN_TYPE)
        conv_limit = (store or {}).get("monthly_limit_conversations", settings.DEFAULT_MONTHLY_CONVERSATION_LIMIT)
        tok_limit  = (store or {}).get("monthly_limit_tokens",        settings.DEFAULT_MONTHLY_TOKEN_LIMIT)

        # Recent 10 conversations
        recent = db.table("conversations").select(
            "id, customer_name, customer_email, status, resolution_type, "
            "message_count, ai_cost_usd, created_at"
        ).eq("owner_id", owner_id).order("created_at", desc=True).limit(10).execute()

        # Daily chart (last 7 days, filled with zeros)
        chart = []
        for i in range(6, -1, -1):
            d   = (date.today() - timedelta(days=i)).isoformat()
            row = next((r for r in usage_rows if r["date"] == d), None)
            chart.append({
                "date":          d,
                "conversations": row["conversations"] if row else 0,
                "tokens":        row["total_tokens"]  if row else 0,
            })

        return {
            # Monthly
            "monthly_conversations":       monthly_conversations,
            "monthly_tokens":              monthly_tokens,
            "monthly_cost_usd":            round(monthly_cost, 4),
            "monthly_limit_conversations": conv_limit,
            "monthly_limit_tokens":        tok_limit,
            "usage_pct_conversations":     min(100, round(100 * monthly_conversations / conv_limit, 1)),
            "usage_pct_tokens":            min(100, round(100 * monthly_tokens / tok_limit, 1)),
            "remaining_conversations":     max(0, conv_limit - monthly_conversations),
            # 7-day
            "conversations_7d":   convs_7d,
            "automated_7d":       automated,
            "escalated_7d":       escalated_count,
            "automation_rate_7d": automation_rate,
            # Plan
            "plan":  plan_type,
            "store": store,
            # Chart + recent
            "chart_7d": chart,
            "recent":   recent.data or [],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"dashboard/metrics error: {e}",
            extra={"request_id": request_id, "user_id": owner_id},
        )
        raise HTTPException(500, "Failed to load metrics")


@router.get("/conversations")
async def list_conversations(
    limit: int = 30,
    offset: int = 0,
    user: dict = Depends(get_user),
):
    owner_id = user["user_id"]
    limit    = min(limit, 100)
    try:
        result = db.table("conversations").select("*").eq(
            "owner_id", owner_id
        ).order("created_at", desc=True).range(offset, offset + limit - 1).execute()
        return {"conversations": result.data or [], "offset": offset, "limit": limit}
    except Exception as e:
        logger.error(f"list_conversations error: {e}", extra={"user_id": owner_id})
        raise HTTPException(500, "Failed to load conversations")


@router.get("/usage")
async def get_usage_breakdown(user: dict = Depends(get_user)):
    """Daily usage for the last 30 days."""
    owner_id  = user["user_id"]
    thirty_ago = (date.today() - timedelta(days=29)).isoformat()
    try:
        result = db.table("usage_daily").select("*").eq(
            "owner_id", owner_id
        ).gte("date", thirty_ago).order("date").execute()
        return {"usage": result.data or []}
    except Exception as e:
        raise HTTPException(500, "Failed to load usage")
