"""
Auth utility endpoints.
Part 5: returns X-Request-ID in all responses via middleware.
"""

from fastapi import APIRouter, Depends, HTTPException
from app.core.auth import get_user
from app.core.clients import db

# ❗ لا نضع /api هنا لأننا نضيفه في main.py
router = APIRouter(prefix="/auth")


@router.get("/me")
async def me(user: dict = Depends(get_user)):
    """Return the authenticated user's profile."""
    try:
        r = (
            db.table("profiles")
            .select("id, email, full_name, plan, created_at")
            .eq("id", user["user_id"])
            .single()
            .execute()
        )

        return {"user": r.data}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
