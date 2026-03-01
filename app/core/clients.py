"""
Centralized external clients initialization.

- Supabase (service role) — backend only
- OpenAI async client
"""

from supabase import create_client, Client
from openai import AsyncOpenAI
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)


def _clean(value: str | None) -> str:
    """
    Clean environment variables from:
    - leading/trailing spaces
    - accidental newlines
    - surrounding quotes
    """
    return (value or "").strip().strip('"').strip("'")


# Clean environment variables
SUPABASE_URL = _clean(settings.SUPABASE_URL)
SUPABASE_SERVICE_ROLE_KEY = _clean(settings.SUPABASE_SERVICE_ROLE_KEY)
OPENAI_API_KEY = _clean(settings.OPENAI_API_KEY)


# Validate presence (fail early with clear message)
if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL is not set")

if not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is not set")

if not OPENAI_API_KEY:
    logger.warning("OPENAI_API_KEY is not set — AI features may fail")


# Create Supabase client (Service Role — backend only)
db: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


# Create OpenAI async client
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
