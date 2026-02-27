"""
Part 4 — Security headers middleware.

Applied globally to every response.

CSP design rationale:
- default-src 'self'         : block everything not explicitly allowed
- script-src 'self' 'unsafe-inline' : needed for inline <script> tags in frontend
- connect-src includes:
    *.supabase.co             : Supabase Auth + REST API
    api.openai.com            : OpenAI API calls from browser (demo uses backend proxy,
                                but CSP must not break any client-side Supabase calls)
    fonts.googleapis.com      : Google Fonts stylesheet
    fonts.gstatic.com         : Google Fonts actual font files
- style-src 'unsafe-inline'  : needed for inline styles in components
- img-src data: https:        : allow base64 images and any HTTPS image
- frame-ancestors 'none'      : same effect as X-Frame-Options: DENY but CSP v3
- upgrade-insecure-requests   : force HTTPS for any HTTP sub-resource

Strict-Transport-Security:
- max-age=31536000 (1 year)
- includeSubDomains
- preload (ready for HSTS preload list when domain is set)
NOTE: Do NOT send HSTS in development — it will break localhost.
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

        # Prevent MIME-type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # Prevent clickjacking (belt + suspenders alongside CSP frame-ancestors)
        response.headers["X-Frame-Options"] = "DENY"

        # Control referrer information
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Disable browser features not needed by this app
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), "
            "payment=(), usb=(), interest-cohort=()"
        )

        # Content Security Policy
        response.headers["Content-Security-Policy"] = CSP_POLICY

        # HSTS — only in production (would break localhost dev)
        if settings.is_production:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains; preload"
            )

        # Remove server fingerprinting headers
        response.headers.pop("server", None)
        response.headers.pop("x-powered-by", None)

        return response
