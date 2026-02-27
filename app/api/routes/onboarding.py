"""
/api/onboarding — Save and retrieve onboarding config.

All writes enforce owner_id = current_user.
store_id is never accepted from the client.
Part 5: request_id attached to logs.
"""
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, validator
from typing import Optional

from app.core.auth import get_user
from app.core.clients import db
from app.core.config import settings, get_plan_limits
from app.api.middleware.logging_middleware import get_request_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/onboarding")


class OnboardingConfig(BaseModel):
    shopify_domain:         Optional[str]  = None
    bot_name:               Optional[str]  = None
    bot_tone:               Optional[str]  = None
    custom_instructions:    Optional[str]  = None
    escalation_threshold:   Optional[int]  = None
    escalation_email:       Optional[str]  = None
    slack_webhook_url:      Optional[str]  = None
    trigger_low_confidence: Optional[bool] = None
    trigger_human_request:  Optional[bool] = None
    trigger_upset_customer: Optional[bool] = None
    trigger_repeat_fail:    Optional[bool] = None
    widget_enabled:         Optional[bool] = None
    onboarding_step:        Optional[int]  = None

    @validator("shopify_domain")
    def check_domain(cls, v):
        if v:
            v = v.strip().lower()
            if not v.endswith(".myshopify.com"):
                raise ValueError("Must end in .myshopify.com")
        return v or None

    @validator("bot_tone")
    def check_tone(cls, v):
        if v and v not in ("friendly", "formal", "casual", "neutral"):
            raise ValueError("Invalid tone value")
        return v

    @validator("escalation_threshold")
    def check_thresh(cls, v):
        if v is not None and not (0 <= v <= 100):
            raise ValueError("Must be 0–100")
        return v

    @validator("bot_name")
    def check_name(cls, v):
        if v:
            v = v.strip()
            if len(v) > 50:
                raise ValueError("Bot name too long (max 50 chars)")
        return v or None

    @validator("custom_instructions")
    def check_instr(cls, v):
        if v and len(v) > 2000:
            raise ValueError("Custom instructions too long (max 2000 chars)")
        return v or None

    @validator("escalation_email")
    def check_email(cls, v):
        if v:
            v = v.strip()
            if "@" not in v or "." not in v.split("@")[-1]:
                raise ValueError("Invalid email address")
        return v or None


@router.get("/get")
async def get_onboarding(user: dict = Depends(get_user)):
    owner_id   = user["user_id"]
    request_id = get_request_id()
    try:
        s_result  = db.table("store_settings").select("*").eq("owner_id", owner_id).execute()
        st_result = db.table("stores").select(
            "shopify_domain, shop_name, oauth_status, product_count, plan_type, "
            "monthly_limit_conversations, monthly_limit_tokens"
        ).eq("owner_id", owner_id).execute()

        return {
            "settings": (s_result.data  or [None])[0],
            "store":    (st_result.data or [None])[0],
        }
    except Exception as e:
        logger.error(
            f"onboarding/get error: {e}",
            extra={"request_id": request_id, "user_id": owner_id},
        )
        raise HTTPException(500, "Failed to load config")


@router.post("/save")
async def save_onboarding(body: OnboardingConfig, user: dict = Depends(get_user)):
    owner_id   = user["user_id"]
    request_id = get_request_id()
    now = datetime.now(timezone.utc).isoformat()

    # Build settings payload — only include supplied fields
    sp = {"owner_id": owner_id, "updated_at": now}
    for field in (
        "bot_name", "bot_tone", "custom_instructions", "escalation_threshold",
        "escalation_email", "slack_webhook_url", "trigger_low_confidence",
        "trigger_human_request", "trigger_upset_customer", "trigger_repeat_fail",
        "widget_enabled", "onboarding_step",
    ):
        val = getattr(body, field)
        if val is not None:
            sp[field] = val

    if body.onboarding_step == 5:
        sp["onboarding_complete"] = True

    try:
        db.table("store_settings").upsert(sp, on_conflict="owner_id").execute()

        if body.shopify_domain:
            plan   = settings.DEFAULT_PLAN_TYPE
            limits = get_plan_limits(plan)

            # Preserve existing plan if already set
            existing = db.table("stores").select("plan_type").eq(
                "owner_id", owner_id
            ).execute()
            if existing.data:
                plan   = existing.data[0].get("plan_type", plan)
                limits = get_plan_limits(plan)

            db.table("stores").upsert(
                {
                    "owner_id":                    owner_id,
                    "shopify_domain":              body.shopify_domain,
                    "oauth_status":                "pending",
                    "plan_type":                   plan,
                    "monthly_limit_conversations": limits["monthly_conversations"],
                    "monthly_limit_tokens":        limits["monthly_tokens"],
                    "updated_at":                  now,
                },
                on_conflict="owner_id",
            ).execute()

        logger.info(
            f"onboarding saved step={body.onboarding_step}",
            extra={"request_id": request_id, "user_id": owner_id},
        )
        return {"ok": True, "step": body.onboarding_step}

    except Exception as e:
        logger.error(
            f"onboarding/save error: {e}",
            extra={"request_id": request_id, "user_id": owner_id},
        )
        raise HTTPException(500, "Failed to save config")
