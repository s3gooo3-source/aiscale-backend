"""
/api/conversations/log — Log widget messages to DB.

All ownership enforced:
- conversation_id verified against owner before use
- store_id fetched by owner_id only, never from client
- Part 5: request_id attached to error logs for correlation
"""
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, validator
from typing import Optional

from app.core.auth import get_user
from app.core.clients import db
from app.core.cost_guard import estimate_cost
from app.core.ownership import (
    get_owned_store_or_none,
    verify_conversation_ownership,
    record_usage,
)
from app.api.middleware.logging_middleware import get_request_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/conversations")


class MessageLog(BaseModel):
    conversation_id: Optional[str] = None
    customer_name:   Optional[str] = None
    customer_email:  Optional[str] = None
    role:            str
    content:         str
    model_used:      Optional[str] = None
    input_tokens:    Optional[int] = 0
    output_tokens:   Optional[int] = 0
    was_escalated:   Optional[bool] = False
    resolution_type: Optional[str] = None

    @validator("role")
    def check_role(cls, v):
        if v not in ("user", "assistant"):
            raise ValueError("Role must be 'user' or 'assistant'")
        return v

    @validator("content")
    def check_content(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("Content cannot be empty")
        return v[:4000]

    @validator("resolution_type")
    def check_resolution(cls, v):
        if v and v not in ("automated", "escalated", "human"):
            raise ValueError("Invalid resolution_type")
        return v


@router.post("/log")
async def log_message(body: MessageLog, user: dict = Depends(get_user)):
    owner_id   = user["user_id"]
    request_id = get_request_id()
    now = datetime.now(timezone.utc).isoformat()

    try:
        # Store fetched by owner_id only — never trust client store_id
        store    = await get_owned_store_or_none(owner_id)
        store_id = str(store["id"]) if store else None

        # Resolve conversation_id securely
        if body.conversation_id:
            await verify_conversation_ownership(body.conversation_id, owner_id)
            conv_id = body.conversation_id
            db.table("conversations").update(
                {"last_message_at": now}
            ).eq("id", conv_id).eq("owner_id", owner_id).execute()
        else:
            result = db.table("conversations").insert({
                "owner_id":        owner_id,
                "store_id":        store_id,
                "customer_name":   (body.customer_name  or "")[:200],
                "customer_email":  (body.customer_email or "")[:200],
                "status":          "active",
                "last_message_at": now,
            }).execute()
            conv_id = result.data[0]["id"]

        in_tok  = body.input_tokens  or 0
        out_tok = body.output_tokens or 0

        # Insert message — owner_id always from auth, never client
        db.table("messages").insert({
            "conversation_id": conv_id,
            "owner_id":        owner_id,
            "role":            body.role,
            "content":         body.content,
            "model_used":      body.model_used,
            "input_tokens":    in_tok,
            "output_tokens":   out_tok,
        }).execute()

        # Update conversation aggregates
        cost   = estimate_cost(in_tok, out_tok)
        update = {
            "message_count":   db.raw("message_count + 1"),
            "input_tokens":    db.raw(f"input_tokens + {in_tok}"),
            "output_tokens":   db.raw(f"output_tokens + {out_tok}"),
            "ai_cost_usd":     db.raw(f"ai_cost_usd + {cost}"),
            "last_message_at": now,
        }
        if body.resolution_type:
            update["resolution_type"] = body.resolution_type
            update["status"] = (
                "resolved" if body.resolution_type == "automated" else "escalated"
            )
        if body.was_escalated:
            update["status"] = "escalated"

        db.table("conversations").update(update).eq(
            "id", conv_id
        ).eq("owner_id", owner_id).execute()

        # Persist usage_daily (non-fatal)
        if in_tok + out_tok > 0:
            await record_usage(owner_id, store_id, in_tok, out_tok, cost)

        return {"ok": True, "conversation_id": conv_id}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"conversations/log error: {e}",
            extra={"request_id": request_id, "user_id": owner_id},
        )
        raise HTTPException(500, "Failed to log message")
