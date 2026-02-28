"""Part 4 — Security headers middleware.

Applied globally to every response.
"""
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from app.core.config import settings


CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' cdn.jsdelivr.net cdnjs.cloudflare.com; "
    "style-src 'self' 'unsafe-inline' fonts.googleapis.com cdn.jsdelivr.net; "
    "font-src 'self' fonts.gstatic.com data:; "
    "img-src 'self' data: https:; "
    "connect-src 'self' "
        "https://*.supabase.co "
        "https://*.supabase.in "
        "https://api.openai.com "
        "https://fonts.googleapis.com "
        "https://fonts.gstatic.com; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "upgrade-insecure-requests"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response: Response = await call_next(request)

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), "
            "usb=(), interest-cohort=()"
        )
        response.headers["Content-Security-Policy"] = CSP_POLICY

        if settings.is_production:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains; preload"
            )

        # Starlette MutableHeaders doesn't implement .pop()
        for h in ("server", "x-powered-by"):
            if h in response.headers:
                del response.headers[h]

        return response
