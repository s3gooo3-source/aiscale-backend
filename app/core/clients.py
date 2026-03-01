import logging
from supabase import create_client, Client
from openai import AsyncOpenAI
from app.core.config import settings

logger = logging.getLogger(__name__)

def _clean(v: str | None) -> str:
    return (v or "").strip()

SUPABASE_URL = _clean(settings.SUPABASE_URL).rstrip("/")
SUPABASE_KEY = _clean(settings.SUPABASE_SERVICE_ROLE_KEY)

if not SUPABASE_URL.startswith("https://") or ".supabase.co" not in SUPABASE_URL:
    raise RuntimeError(f"Bad SUPABASE_URL: {SUPABASE_URL!r}")

# أهم سطر: حذف أي مسافات/أسطر داخل المفتاح
SUPABASE_KEY = SUPABASE_KEY.replace("\n", "").replace("\r", "").strip()

if not SUPABASE_KEY.startswith("eyJ"):
    raise RuntimeError("Bad SUPABASE_SERVICE_ROLE_KEY: must be Legacy JWT starting with 'eyJ'")

db: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

openai_client = AsyncOpenAI(api_key=_clean(settings.OPENAI_API_KEY))
