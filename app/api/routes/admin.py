"""
/api/admin — Internal admin endpoints.

Part 3 hardening:
- APP_SECRET_KEY required in production (validated at startup)
- ADMIN_ALLOWED_IPS: optional IP allowlist
- DISABLE_ADMIN_ROUTES: kills all admin routes at startup
- Still uses x-admin-key header for authentication
"""
import logging
from datetime import date
from fastapi import APIRouter, HTTPException, Header, Request, Depends
from pydantic import BaseModel

from app.core.config import settings, get_plan_limits, PLAN_LIMITS
from app.core.clients import db
from app.core.cost_guard import get_today_total_cost

logger = logging.getLogger(__name__)

# Conditionally register router — if disabled, all routes return 404
router = APIRouter(prefix="/api/admin")


def _get_client_ip(request: Request) -> str:
    return (
        request.headers.get("x-forwarded-for", request.client.host or "unknown")
        .split(",")[0]
        .strip()
    )


def require_admin(request: Request, x_admin_key: str | None = Header(default=None, alias="x-admin-key")) -> None:
    """
    Dependency that enforces:
    1. Admin routes not disabled
    2. IP allowlist (if configured)
    3. Valid x-admin-key header
    """
    # 1. Kill switch for entire admin surface
    if settings.DISABLE_ADMIN_ROUTES:
        raise HTTPException(404, "Not found")

    # 2. IP allowlist check
    allowed_ips = settings.admin_allowed_ips  # list property from config
    if allowed_ips:
        client_ip = _get_client_ip(request)
        if client_ip not in allowed_ips:
            logger.warning(
                f"Admin access denied: IP {client_ip!r} not in allowlist"
            )
            # Return 404, not 403 — don't confirm admin routes exist
            raise HTTPException(404, "Not found")

    # 3. Key validation
    if not x_admin_key:
        raise HTTPException(403, "Admin key required")

    if not settings.APP_SECRET_KEY:
        # Should never reach here in production due to startup validation
        raise HTTPException(503, "Admin authentication not configured")

    # Constant-time comparison to prevent timing attacks
    import hmac
    if not hmac.compare_digest(
        x_admin_key.encode(),
        settings.APP_SECRET_KEY.encode()
    ):
        logger.warning("Admin access attempt with invalid key")
        raise HTTPException(403, "Invalid admin key")


# ── Endpoints ────────────────────────────────────────────────

@router.get("/cost/today")
async def today_cost(deps=Depends(require_admin)):
    """Current-day total AI cost across all users + demo. Kill switch status."""
    today_cost_val = await get_today_total_cost()
    return {
        "date":              date.today().isoformat(),
        "total_cost_usd":    round(today_cost_val, 6),
        "threshold_usd":     settings.MAX_DAILY_COST_USD,
        "kill_switch_active": today_cost_val >= settings.MAX_DAILY_COST_USD,
        "demo_included":     True,
    }


@router.get("/cost/breakdown")
async def cost_breakdown(deps=Depends(require_admin)):
    """Per-owner cost breakdown for today."""
    today = date.today().isoformat()
    try:
        rows = db.table("usage_daily").select(
            "owner_id, conversations, total_tokens, estimated_cost_usd"
        ).eq("date", today).order("estimated_cost_usd", desc=True).execute()

        return {
            "date": today,
            "rows": rows.data or [],
            "total_cost_usd": round(
                sum(float(r.get("estimated_cost_usd", 0)) for r in (rows.data or [])), 6
            ),
        }
    except Exception as e:
        raise HTTPException(500, str(e))


class UpgradePlan(BaseModel):
    user_id: str
    new_plan: str


@router.post("/plan/upgrade")
async def upgrade_plan(body: UpgradePlan, deps=Depends(require_admin)):
    """Upgrade a user's plan. Updates both profiles and stores tables."""
    if body.new_plan not in PLAN_LIMITS:
        raise HTTPException(400, f"Unknown plan: {body.new_plan!r}. Valid: {list(PLAN_LIMITS)}")

    limits = get_plan_limits(body.new_plan)
    try:
        db.table("profiles").update({"plan": body.new_plan}).eq("id", body.user_id).execute()
        db.table("stores").update({
            "plan_type":                    body.new_plan,
            "monthly_limit_conversations":  limits["monthly_conversations"],
            "monthly_limit_tokens":         limits["monthly_tokens"],
        }).eq("owner_id", body.user_id).execute()
        logger.info(f"Plan upgraded: user={body.user_id} plan={body.new_plan}")
        return {"ok": True, "plan": body.new_plan, "limits": limits}
    except Exception as e:
        logger.error(f"admin/plan/upgrade: {e}")
        raise HTTPException(500, str(e))


@router.get("/usage/overview")
async def usage_overview(deps=Depends(require_admin)):
    """All-user usage summary for today and current month."""
    today       = date.today().isoformat()
    month_start = date.today().replace(day=1).isoformat()
    try:
        today_rows = db.table("usage_daily").select(
            "owner_id, conversations, total_tokens, estimated_cost_usd"
        ).eq("date", today).execute()

        month_rows = db.table("usage_daily").select(
            "estimated_cost_usd"
        ).gte("date", month_start).execute()

        today_data = today_rows.data or []
        return {
            "today": {
                "users_active":   len(today_data),
                "total_convs":    sum(r.get("conversations", 0)           for r in today_data),
                "total_tokens":   sum(r.get("total_tokens", 0)            for r in today_data),
                "total_cost_usd": round(sum(float(r.get("estimated_cost_usd", 0)) for r in today_data), 6),
                "demo_included":  any(r.get("owner_id") == "00000000-0000-0000-0000-000000000000" for r in today_data),
            },
            "month": {
                "total_cost_usd": round(
                    sum(float(r.get("estimated_cost_usd", 0)) for r in (month_rows.data or [])), 6
                ),
            },
            "kill_switch_threshold_usd": settings.MAX_DAILY_COST_USD,
            "admin_ip_allowlist_active":  bool(settings.admin_allowed_ips),
        }
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/health/config")
async def config_health(deps=Depends(require_admin)):
    """Returns sanitised configuration status — no secrets exposed."""
    return {
        "environment":           settings.ENVIRONMENT,
        "admin_routes_enabled":  not settings.DISABLE_ADMIN_ROUTES,
        "ip_allowlist_enabled":  bool(settings.admin_allowed_ips),
        "ip_allowlist_count":    len(settings.admin_allowed_ips),
        "redis_connected":       settings.REDIS_URL is not None,
        "sentry_enabled":        settings.SENTRY_DSN is not None,
        "max_daily_cost_usd":    settings.MAX_DAILY_COST_USD,
        "secret_key_length":     len(settings.APP_SECRET_KEY),
        "secret_key_strong":     len(settings.APP_SECRET_KEY) >= 32,
    }
