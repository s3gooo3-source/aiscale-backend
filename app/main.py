"""
Request logging middleware.

- Adds x-request-id header
- Logs request/response with timing
- Safely reads/removes optional cost headers (no .pop on MutableHeaders)
"""

from __future__ import annotations

import time
import uuid
import logging
from typing import Callable, Optional

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start = time.perf_counter()

        # Request ID
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = request_id

        try:
            response: Response = await call_next(request)
        except Exception:
            # Log exception with request context then re-raise
            logger.exception(
                "Unhandled exception",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": str(request.url.path),
                },
            )
            raise

        # Timing
        duration_ms = int((time.perf_counter() - start) * 1000)

        # Optional cost headers (safe remove)
        cost_usd: Optional[str] = response.headers.get("x-cost-usd")
        if "x-cost-usd" in response.headers:
            del response.headers["x-cost-usd"]

        cost_tokens: Optional[str] = response.headers.get("x-cost-tokens")
        if "x-cost-tokens" in response.headers:
            del response.headers["x-cost-tokens"]

        # Add response headers
        response.headers["x-request-id"] = request_id
        response.headers["x-response-time-ms"] = str(duration_ms)

        # Log line
        logger.info(
            "request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": str(request.url.path),
                "status_code": getattr(response, "status_code", None),
                "duration_ms": duration_ms,
                "cost_usd": cost_usd,
                "cost_tokens": cost_tokens,
                "client": request.client.host if request.client else None,
            },
        )

        return response


# Backwards-compat alias (if any old import uses it)
LoggingMiddleware = RequestLoggingMiddleware
