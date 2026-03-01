import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)

# لازم يكون فيه متغير اسمه app عشان uvicorn app.main:app يشتغل
app = FastAPI(title="AIScale Pro API")

# (اختياري) CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Middlewares
from app.api.middleware.security_headers import SecurityHeadersMiddleware
from app.api.middleware.logging_middleware import LoggingMiddleware

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(LoggingMiddleware)

# Routers (عدّل أسماء الموديولات حسب الموجود عندك داخل app/api/routes)
from app.api.routes import auth

app.include_router(auth.router, prefix="/api")
