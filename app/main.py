import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)

# ✅ لازم app يكون هنا كمتغير عالمي
app = FastAPI(title="AIScale Pro API")

# --- CORS (اختياري) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Middleware imports (محمي عشان لا يكسر التشغيل لو اسم الكلاس مختلف) ---
try:
    from app.api.middleware.security_headers import SecurityHeadersMiddleware
    app.add_middleware(SecurityHeadersMiddleware)
except Exception as e:
    logger.warning(f"SecurityHeadersMiddleware not loaded: {e}")

try:
    # IMPORTANT: قد يكون اسم الكلاس عندك مختلف
    from app.api.middleware.logging_middleware import LoggingMiddleware
    app.add_middleware(LoggingMiddleware)
except Exception as e:
    logger.warning(f"Logging middleware not loaded: {e}")

# --- Routers ---
try:
    from app.api.routes import auth, users, health, ai, billing  # عدّل حسب الموجود عندك
    app.include_router(auth.router, prefix="/api")
    app.include_router(users.router, prefix="/api")
    app.include_router(health.router, prefix="/api")
    app.include_router(ai.router, prefix="/api")
    app.include_router(billing.router, prefix="/api")
except Exception as e:
    logger.warning(f"Routers not fully loaded: {e}")

@app.get("/")
def root():
    return {"status": "ok"}
