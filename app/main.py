"""
AIScale Pro — Application entry point.

Startup order:
1. validate_for_startup() — fail fast on bad config (Part 3)
2. configure_json_logging() — structured JSON logs (Part 5)
3. init_sentry()            — optional error tracking (Part 5)
4. FastAPI app with:
   - CORSMiddleware
   - SecurityHeadersMiddleware (Part 4)
   - RequestLoggingMiddleware  (Part 5)
5. All routers mounted
"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.sentry import init_sentry
from app.api.middleware.security_headers import SecurityHeadersMiddleware
from app.api.middleware.logging_middleware import (
    RequestLoggingMiddleware,
    configure_json_logging,
)
from app.api.routes import (
    health, auth, demo, chat,
    dashboard, onboarding, conversations, admin,
)

# ── Step 1: Validate config BEFORE any other import side-effects ──
settings.validate_for_startup()

# ── Step 2: Configure JSON structured logging ─────────────────
configure_json_logging(level=logging.DEBUG if not settings.is_production else logging.INFO)
logger = logging.getLogger(__name__)

# ── Step 3: Sentry (no-op if DSN not set) ─────────────────────
init_sentry()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "AIScale Pro API starting",
        extra={
            "env":            settings.ENVIRONMENT,
            "kill_switch":    settings.MAX_DAILY_COST_USD,
            "admin_routes":   not settings.DISABLE_ADMIN_ROUTES,
            "admin_ip_list":  bool(settings.admin_allowed_ips),
            "redis":          settings.REDIS_URL is not None,
            "sentry":         settings.SENTRY_DSN is not None,
        }
    )
    yield
    logger.info("AIScale Pro API shutdown")


# ── FastAPI app ───────────────────────────────────────────────
app = FastAPI(
    title="AIScale Pro API",
    version="0.3.0-beta",
    lifespan=lifespan,
    docs_url="/api/docs"   if not settings.is_production else None,
    redoc_url="/api/redoc" if not settings.is_production else None,
    openapi_url="/api/openapi.json" if not settings.is_production else None,
)

# ── Middleware stack (applied in reverse order — last added = outermost) ──

# 1. CORS (must be outermost to handle preflight before other middleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],  # allow frontend to read request ID
)

# 2. Security headers (Part 4) — added after CORS so CORS preflight isn't blocked
app.add_middleware(SecurityHeadersMiddleware)

# 3. Request logging + request ID (Part 5) — innermost, after headers applied
app.add_middleware(RequestLoggingMiddleware)

# ── Routers ──────────────────────────────────────────────────
for module in [health, auth, demo, chat, dashboard, onboarding, conversations, admin]:
    app.include_router(module.router)


@app.get("/")
async def root():
    return {"service": "AIScale Pro API", "version": "0.3.0-beta"}
