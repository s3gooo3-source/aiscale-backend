from supabase import create_client, Client
from openai import AsyncOpenAI
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

# Service role client — bypasses RLS. Backend use ONLY.
# Never expose this token to the frontend.
db: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)

# OpenAI async client
openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
