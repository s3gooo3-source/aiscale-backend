import sys
import logging
from pydantic_settings import BaseSettings
from pydantic import validator
from typing import List, Optional

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    # ── Supabase ──────────────────────────────────────────────
    SUPABASE_URL: str
    SUPABASE_ANON_KEY: str
    SUPABASE_SERVICE_ROLE_KEY: str

    # ── OpenAI ────────────────────────────────────────────────
    OPENAI_API_KEY: str
    DEMO_MODEL: str = "gpt-4o-mini"
    PREMIUM_MODEL: str = "gpt-4o"

    # ── App ───────────────────────────────────────────────────
    APP_SECRET_KEY: str = ""          # REQUIRED in production — empty = startup fail
    ALLOWED_ORIGINS: str = "http://localhost:8080"
    ENVIRONMENT: str = "development"

    # ── Admin hardening (Part 3) ──────────────────────────────
    ADMIN_ALLOWED_IPS: str = ""       # comma-separated; empty = all IPs allowed
    DISABLE_ADMIN_ROUTES: bool = False

    # ── Redis (optional) ─────────────────────────────────────
    REDIS_URL: Optional[str] = None

    # ── Sentry (optional) ────────────────────────────────────
    SENTRY_DSN: Optional[str] = None

    # ── Public demo rate limits ───────────────────────────────
    DEMO_RATE_LIMIT_PER_IP: int = 20
    DEMO_RATE_LIMIT_PER_SESSION: int = 10

    # ── Authenticated rate limits ─────────────────────────────
    AUTH_RATE_LIMIT_PER_MINUTE: int = 10
    IP_RATE_LIMIT_PER_MINUTE: int = 5

    # ── Token guardrails ──────────────────────────────────────
    MAX_OUTPUT_TOKENS: int = 500
    MAX_INPUT_CHARS: int = 2000

    # ── Cost guardrails ───────────────────────────────────────
    MAX_DAILY_COST_USD: float = 20.0
    COST_PER_INPUT_TOKEN: float = 0.00000015   # gpt-4o-mini $0.15/1M
    COST_PER_OUTPUT_TOKEN: float = 0.0000006   # gpt-4o-mini $0.60/1M

    # ── Plan defaults ─────────────────────────────────────────
    DEFAULT_PLAN_TYPE: str = "free_beta"
    DEFAULT_MONTHLY_CONVERSATION_LIMIT: int = 100
    DEFAULT_MONTHLY_TOKEN_LIMIT: int = 50000

    # ── Cache ─────────────────────────────────────────────────
    FAQ_CACHE_TTL_HOURS: int = 24

    @property
    def origins(self) -> List[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def admin_allowed_ips(self) -> List[str]:
        return [ip.strip() for ip in self.ADMIN_ALLOWED_IPS.split(",") if ip.strip()]

    def validate_for_startup(self) -> None:
        """
        Called at application startup.
        Kills the process with a clear message if required production
        settings are missing — fail-fast before accepting any traffic.
        """
        errors = []

        if self.is_production:
            if not self.APP_SECRET_KEY or self.APP_SECRET_KEY == "change-me-in-production":
                errors.append(
                    "APP_SECRET_KEY must be set to a strong secret in production. "
                    "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
                )
            if len(self.APP_SECRET_KEY) < 32:
                errors.append(
                    f"APP_SECRET_KEY is too short ({len(self.APP_SECRET_KEY)} chars). "
                    "Must be at least 32 characters in production."
                )

        if errors:
            for e in errors:
                logger.critical(f"STARTUP VALIDATION FAILED: {e}")
            sys.exit(
                "\n\n[AIScale Pro] Cannot start — configuration errors:\n"
                + "\n".join(f"  ✗ {e}" for e in errors)
                + "\n"
            )

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()

# ── Plan limit table ──────────────────────────────────────────
PLAN_LIMITS: dict = {
    "free_beta": {"monthly_conversations": 100,    "monthly_tokens": 50_000},
    "starter":   {"monthly_conversations": 2_000,  "monthly_tokens": 1_000_000},
    "growth":    {"monthly_conversations": 20_000, "monthly_tokens": 10_000_000},
}

def get_plan_limits(plan: str) -> dict:
    return PLAN_LIMITS.get(plan, PLAN_LIMITS["free_beta"])
