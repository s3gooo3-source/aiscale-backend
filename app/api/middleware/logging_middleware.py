"""
Request / Response logging middleware.
"""

import time
import logging
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

logger = logging.getLogger(__name__)


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        start_time = time.time()

        try:
            response: Response = await call_next(request)
        except Exception as e:
            logger.exception("Unhandled exception during request")
            raise e

        process_time = time.time() - start_time

        # Read custom cost header safely (MutableHeaders does NOT support pop)
        cost_usd = response.headers.get("x-cost-usd")

        if "x-cost-usd" in response.headers:
            del response.headers["x-cost-usd"]

        # Log request details
        logger.info(
            "HTTP %s %s | Status: %s | Time: %.3fs | Cost: %s",
            request.method,
            request.url.path,
            response.status_code,
            process_time,
            cost_usd or "N/A",
        )

        return response
