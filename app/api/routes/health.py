"""
/api/health — Liveness + readiness probe.
Used by Railway, load balancers, and monitoring tools.
Returns version 0.3.0 to match hardened build.
"""
from fastapi import APIRouter
from datetime import datetime, timezone
from app.core.config import settings

router = APIRouter()


@router.get("/api/health")
async def health():
    return {
        "status":      "ok",
        "version":     "0.3.0-beta",
        "environment": settings.ENVIRONMENT,
        "time":        datetime.now(timezone.utc).isoformat(),
    }


@router.get("/api/ready")
async def ready():
    """Readiness probe — checks DB connectivity."""
    from app.core.clients import db
    try:
        db.table("profiles").select("id").limit(1).execute()
        return {"status": "ready"}
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(503, f"Database not ready: {e}")
