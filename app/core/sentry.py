"""
Optional Sentry integration.

Enabled only when SENTRY_DSN is set in environment.
If DSN is missing, all functions are no-ops — zero overhead.

Usage in routes:
    from app.core.sentry import capture_exception
    try:
        ...
    except Exception as e:
        capture_exception(e)
        raise
"""
import logging
from typing import Optional
from app.core.config import settings

logger = logging.getLogger(__name__)
_sentry_initialized = False


def init_sentry() -> None:
    """
    Call once at startup. No-op if SENTRY_DSN not set.
    Captures unhandled exceptions and 500-level errors automatically.
    """
    global _sentry_initialized
    dsn = settings.SENTRY_DSN
    if not dsn:
        logger.info("Sentry: disabled (SENTRY_DSN not set)")
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.asgi import SentryAsgiMiddleware
        from sentry_sdk.integrations.logging import LoggingIntegration

        sentry_sdk.init(
            dsn=dsn,
            environment=settings.ENVIRONMENT,
            traces_sample_rate=0.1 if settings.is_production else 0.0,
            profiles_sample_rate=0.0,
            integrations=[
                LoggingIntegration(
                    level=logging.WARNING,
                    event_level=logging.ERROR,
                ),
            ],
            # Don't send PII by default
            send_default_pii=False,
        )
        _sentry_initialized = True
        logger.info(f"Sentry: initialised (env={settings.ENVIRONMENT})")
    except ImportError:
        logger.warning("Sentry: sentry-sdk not installed — skipping")
    except Exception as e:
        logger.warning(f"Sentry: init failed ({e}) — continuing without Sentry")


def capture_exception(exc: Exception) -> None:
    """Capture an exception to Sentry if initialised."""
    if not _sentry_initialized:
        return
    try:
        import sentry_sdk
        sentry_sdk.capture_exception(exc)
    except Exception:
        pass


def set_user_context(user_id: str, email: Optional[str] = None) -> None:
    """Attach user context to current Sentry scope."""
    if not _sentry_initialized:
        return
    try:
        import sentry_sdk
        sentry_sdk.set_user({"id": user_id, "email": email})
    except Exception:
        pass
