"""
Part 5 — Structured JSON logging + Request ID middleware.

Every request produces a log line like:
{
  "ts":         "2025-01-15T10:23:41.123Z",
  "level":      "INFO",
  "request_id": "req_7f3a9b2c",
  "method":     "POST",
  "path":       "/api/chat/message",
  "status":     200,
  "duration_ms": 312,
  "user_id":    "uuid...",
  "ip":         "1.2.3.4",
  "ua":         "Mozilla/5.0...",
  "cost_usd":   0.000042
}

Request ID is also returned in the X-Request-ID response header
so frontend/support can correlate logs with user reports.
"""
import json
import time
import uuid
import logging
from typing import Callable, Optional
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp


# ── JSON log formatter ────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    """Emit one JSON object per log record."""

    def format(self, record: logging.LogRecord) -> str:
        log: dict = {
            "ts":    self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg":   record.getMessage(),
        }
        # Attach any extra fields attached to the record
        for key in ("request_id", "user_id", "store_id", "path",
                    "method", "status", "duration_ms", "cost_usd", "ip"):
            if hasattr(record, key):
                log[key] = getattr(record, key)

        if record.exc_info:
            log["exc"] = self.formatException(record.exc_info)

        return json.dumps(log, default=str)


def configure_json_logging(level: int = logging.INFO) -> None:
    """
    Replace the default basicConfig format with JSON.
    Call once at application startup.
    """
    formatter = JsonFormatter()
    root = logging.getLogger()
    root.setLevel(level)

    # Replace all existing handlers
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root.addHandler(handler)

    # Quiet noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


# ── Request context storage ───────────────────────────────────
# Simple in-process store. Not shared across workers — that's fine
# because the request_id is returned to the client via header anyway.
import contextvars
_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)
_user_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "user_id", default=None
)


def get_request_id() -> str:
    return _request_id_var.get("-")


def set_request_context(request_id: str, user_id: Optional[str] = None) -> None:
    _request_id_var.set(request_id)
    if user_id:
        _user_id_var.set(user_id)


# ── Middleware ────────────────────────────────────────────────
class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Per-request structured logging.

    Reads:
    - Authorization header (to extract user_id for logging only — no auth)
    - x-forwarded-for for true client IP
    - x-cost-usd response header (set by AI routes for per-request cost)
    """

    def __init__(self, app: ASGIApp):
        super().__init__(app)
        self.logger = logging.getLogger("aiscale.request")

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = f"req_{uuid.uuid4().hex[:8]}"
        start_time = time.perf_counter()

        # Inject request_id into context
        _request_id_var.set(request_id)

        # Best-effort user_id from Authorization header (JWT sub)
        user_id = self._extract_user_id(request)
        if user_id:
            _user_id_var.set(user_id)

        client_ip = (
            request.headers.get("x-forwarded-for", getattr(request.client, "host", "unknown"))
            .split(",")[0]
            .strip()
        )

        # Process request
        response: Response = await call_next(request)

        duration_ms = round((time.perf_counter() - start_time) * 1000, 1)

        # Attach request ID to response so clients can reference it
        response.headers["X-Request-ID"] = request_id

        # Cost annotation (set by AI endpoints)
        cost_usd = response.headers.pop("x-cost-usd", None)

        # Build log record
        extra = {
            "request_id":  request_id,
            "method":      request.method,
            "path":        request.url.path,
            "status":      response.status_code,
            "duration_ms": duration_ms,
            "ip":          client_ip,
            "ua":          request.headers.get("user-agent", "")[:120],
        }
        if user_id:
            extra["user_id"] = user_id
        if cost_usd:
            try:
                extra["cost_usd"] = float(cost_usd)
            except ValueError:
                pass

        log_level = logging.INFO
        if response.status_code >= 500:
            log_level = logging.ERROR
        elif response.status_code >= 400:
            log_level = logging.WARNING

        self.logger.log(
            log_level,
            f"{request.method} {request.url.path} → {response.status_code} ({duration_ms}ms)",
            extra=extra,
        )

        return response

    @staticmethod
    def _extract_user_id(request: Request) -> Optional[str]:
        """Best-effort JWT sub extraction for logging — no verification."""
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return None
        try:
            import base64
            token = auth[7:]
            parts = token.split(".")
            if len(parts) != 3:
                return None
            payload_raw = parts[1]
            # Pad base64
            payload_raw += "=" * (4 - len(payload_raw) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_raw))
            return payload.get("sub")
        except Exception:
            return None
