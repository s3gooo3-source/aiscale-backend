"""
Auth utility endpoints.
Part 5: returns X-Request-ID in all responses via middleware.
"""

from fastapi import APIRouter, Depends, HTTPException
from app.core.auth import get_user
from app.core.clients import db

# ❗ لا نضع /api هنا لأننا نضيفه في main.py
router = APIRouter(prefix="/auth", tags=["auth"])


def _extract_user_id(user: dict) -> str | None:
    """
    يحاول يطلع user id من أكثر من شكل شائع:
    - {"user_id": "..."}
    - {"id": "..."}
    - {"sub": "..."}  (JWT subject)
    - {"user": {"id": "..."}}
    """
    if not isinstance(user, dict):
        return None

    if user.get("user_id"):
        return user["user_id"]
    if user.get("id"):
        return user["id"]
    if user.get("sub"):
        return user["sub"]

    nested = user.get("user")
    if isinstance(nested, dict) and nested.get("id"):
        return nested["id"]

    return None


@router.get("/me")
async def me(user: dict = Depends(get_user)):
    """Return the authenticated user's profile."""
    user_id = _extract_user_id(user)

    if not user_id:
        # هنا نعرف 100% أن المشكلة من get_user أو التوكن
        raise HTTPException(
            status_code=401,
            detail="Invalid token payload: user id not found (expected user_id/id/sub).",
        )

    try:
        r = (
            db.table("profiles")
            .select("id, email, full_name, plan, created_at")
            .eq("id", user_id)
            .single()
            .execute()
        )

        if not r.data:
            raise HTTPException(status_code=404, detail="Profile not found for this user.")

        return {"user": r.data}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
