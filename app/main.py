# app/main.py

from dotenv import load_dotenv
load_dotenv()

import os
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.core.database import db
from app.utils.logger import logger


# ──────────────────────────────────────────────────────────
# PRODUCTION SAFETY GUARD
# ──────────────────────────────────────────────────────────
# This build ships with authentication stubbed out (every request runs as a
# shared demo user — see app/core/dependencies.py) and defaults many OAuth
# callback / API base URLs at a real-looking production domain. That's a
# reasonable default for local/dev use, but must never boot silently in a
# real production deployment without a conscious opt-in. Fail fast instead.
if settings.ENV == "production" and not settings.ALLOW_DEMO_AUTH_IN_PRODUCTION:
    raise RuntimeError(
        "Refusing to start with ENV=production: this build's authentication "
        "layer is stubbed out (app/core/dependencies.py always returns a demo "
        "user) and is NOT safe to expose to an untrusted network as-is. "
        "Either wire up real authentication before deploying, or explicitly "
        "acknowledge the risk by setting ALLOW_DEMO_AUTH_IN_PRODUCTION=true."
    )
if settings.ENV == "production" and not settings.ENCRYPTION_KEY:
    raise RuntimeError(
        "Refusing to start with ENV=production: ENCRYPTION_KEY is not set. "
        "Set a dedicated ENCRYPTION_KEY so stored social tokens aren't "
        "encrypted with a key derived from SECRET_KEY."
    )


MAX_REQUEST_BODY_BYTES = 5 * 1024 * 1024  # 5 MB — generous for JSON/text payloads


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Rejects requests whose declared Content-Length exceeds the cap before
    the body is read, and defensively aborts oversized bodies sent without a
    Content-Length (e.g. chunked transfer-encoding)."""

    async def dispatch(self, request: Request, call_next) -> Response:
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > MAX_REQUEST_BODY_BYTES:
                    return Response(
                        content='{"detail":"Request body too large."}',
                        status_code=413,
                        media_type="application/json",
                    )
            except ValueError:
                pass
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        if settings.ENV == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
        return response

# ── Routers ───────────────────────────────────────────────
from app.api.v1 import (
    ai_generation, content,
    social_media, scheduling, analytics,
    brand, campaigns, media, history
)
from app.api.v1 import dashboard, templates, chat, admin_usage
from app.api.v1 import trends

# ── Services ──────────────────────────────────────────────
from app.services.scheduler_service import scheduler_service
from app.services.generation_memory import prune_history
from app.services.instagram_service import refresh_expiring_instagram_tokens
from app.core.database import get_db

# ── Middleware ────────────────────────────────────────────
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.csrf import CSRFMiddleware
from app.middleware.error_handler import (
    validation_exception_handler,
    http_exception_handler,
    general_exception_handler,
)

# ──────────────────────────────────────────────────────────
# CREATE APP
# ──────────────────────────────────────────────────────────
app = FastAPI(
    title=settings.APP_NAME,
    debug=settings.DEBUG,
    version="3.0.0",
    description=(
        "ContentStudio AI — One AI workspace to plan, create, and publish "
        "on-brand content everywhere. Built for founders, marketing teams, "
        "agencies, and growth teams."
    ),
    docs_url="/docs" if settings.ENV != "production" else None,
    redoc_url="/redoc" if settings.ENV != "production" else None,
    openapi_url="/openapi.json" if settings.ENV != "production" else None,
    openapi_tags=[
        {"name": "Health",                "description": "Health & status checks"},
        {"name": "Brand Intelligence",    "description": "Brand profiles — tone, voice, vocabulary, CTAs"},
        {"name": "Campaign Management",   "description": "Campaign-first content workflow & editorial calendar"},
        {"name": "AI Content Generation", "description": "Blog, tweet, email, captions, repurposing, scoring"},
        {"name": "Content Management",    "description": "Content library — CRUD, export, search"},
        {"name": "Content Templates",     "description": "Pre-built brand-aware content templates"},
        {"name": "Media Generation",       "description": "AI image generation (NVIDIA FLUX) + social carousels (Gamma AI)"},
        {"name": "Social Media",          "description": "Connect accounts & publish posts"},
        {"name": "Scheduling",            "description": "Hootsuite/Buffer-style scheduler — queue, bulk, AI-generate, best times, calendar"},
        {"name": "Analytics",             "description": "Engagement metrics & performance trends"},
        {"name": "Dashboard",             "description": "Home-screen stats & activity feed"},
        {"name": "AI Chat Agent",         "description": "Conversational AI agent — thread-based chat with full content tool access"},
        {"name": "Admin Observability",   "description": "Runtime health, cache stats, and platform generation profiles"},
    ],
)

# ──────────────────────────────────────────────────────────
# CORS
# ──────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
)

# Security headers — HSTS, X-Frame-Options, X-Content-Type-Options, etc.
app.add_middleware(SecurityHeadersMiddleware)

# Rate limiting — 100 req/min for regular users; billing & auth are generous
app.add_middleware(RateLimitMiddleware, requests_per_minute=100)

# CSRF protection — validates Origin/Referer for cookie-authenticated state-changing requests
app.add_middleware(CSRFMiddleware)

# Body size limit — added last so it runs first (outermost), rejecting
# oversized requests before they reach rate limiting/CSRF/routing.
app.add_middleware(BodySizeLimitMiddleware)

# Exception handlers
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(Exception, general_exception_handler)


# ──────────────────────────────────────────────────────────
# STARTUP / SHUTDOWN
# ──────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup_event():
    logger.info("🚀 Starting ContentStudio AI Backend v3.0")
    logger.info(f"Environment: {settings.ENV} | Debug: {settings.DEBUG}")

    db.connect()

    # ── Redis / Celery health-check ──────────────────────────────────────────
    if settings.REDIS_URL:
        try:
            import redis as _redis
            _r = _redis.Redis.from_url(
                settings.REDIS_URL,
                socket_timeout=3.0,
                socket_connect_timeout=3.0,
            )
            _r.ping()
            logger.info(f"✅ Redis connected: {settings.REDIS_URL[:30]}...")
            logger.info("🔄 Post scheduling mode: Celery (reliable, persists across restarts)")
        except Exception as e:
            logger.warning(f"⚠️ Redis not reachable at startup ({e}). Cache falls back to in-process; post scheduling falls back to APScheduler.")
    else:
        logger.info("ℹ️ REDIS_URL not set — using in-process LRU cache + APScheduler (dev mode)")

    await scheduler_service.load_pending_posts()

    scheduler_service.scheduler.add_job(
        prune_history,
        "interval",
        days=30,
        id="history_prune",
        replace_existing=True,
    )
    logger.info("🧹 History pruning job scheduled (every 30 days)")

    async def _refresh_instagram_tokens():
        db = get_db()
        await refresh_expiring_instagram_tokens(db, within_days=7)

    scheduler_service.scheduler.add_job(
        _refresh_instagram_tokens,
        "interval",
        days=7,
        id="instagram_refresh",
        replace_existing=True,
    )
    logger.info("📱 Instagram token refresh job scheduled (every 7 days)")

    logger.info("✅ All services initialised")
    logger.info(f"🌐 http://{settings.HOST}:{settings.PORT}")
    logger.info("📚 API Docs: /docs  |  ReDoc: /redoc")


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("👋 Shutting down ContentStudio AI...")
    scheduler_service.shutdown()
    logger.info("✅ Graceful shutdown complete")


# ──────────────────────────────────────────────────────────
# HEALTH
# ──────────────────────────────────────────────────────────
@app.get("/", tags=["Health"])
async def root():
    return {
        "status": "ok",
        "product": "ContentStudio AI",
        "version": "3.0.0",
        "docs": "/docs",
        "redoc": "/redoc",
    }


@app.get("/health", tags=["Health"])
async def health():
    # Quick non-blocking Redis ping
    redis_status = "disabled"
    if settings.REDIS_URL:
        try:
            import redis as _redis
            _r = _redis.Redis.from_url(
                settings.REDIS_URL,
                socket_timeout=1.0,
                socket_connect_timeout=1.0,
            )
            _r.ping()
            redis_status = "connected"
        except Exception:
            redis_status = "unreachable"

    scheduler_mode = "celery" if (settings.REDIS_URL and redis_status == "connected") else "apscheduler"

    return {
        "status": "healthy",
        "environment": settings.ENV,
        "version": "3.0.0",
        "services": {
            "database":  "connected",
            "scheduler": f"active ({scheduler_mode})",
            "redis":     redis_status,
            "celery":    "worker-separate-process" if redis_status == "connected" else "disabled",
            "ai":        "ready",
        },
    }


@app.get("/api/v1/tasks/{task_id}", tags=["Health"])
async def get_task_status(task_id: str):
    """
    Poll the status of a background Celery task (e.g. multi-platform generation).

    States: PENDING → STARTED → SUCCESS | FAILURE | REVOKED
    When state == SUCCESS, `result` contains the task output.
    """
    if not settings.REDIS_URL:
        raise HTTPException(status_code=503, detail="Task backend (Redis) is not configured")
    try:
        from app.celery_app import celery_app as _celery
        task = _celery.AsyncResult(task_id)
        response: dict = {"task_id": task_id, "state": task.state}
        if task.state == "SUCCESS":
            response["result"] = task.result
        elif task.state == "FAILURE":
            response["error"] = str(task.result)
        elif task.state == "STARTED":
            response["info"] = task.info or {}
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not retrieve task: {e}")


# ──────────────────────────────────────────────────────────
# ROUTERS
# ──────────────────────────────────────────────────────────
PREFIX = "/api/v1"

app.include_router(brand.router,          prefix=PREFIX)
app.include_router(campaigns.router,      prefix=PREFIX)
app.include_router(ai_generation.router,  prefix=PREFIX)
app.include_router(content.router,        prefix=PREFIX)
app.include_router(templates.router,      prefix=PREFIX)
app.include_router(social_media.router,   prefix=PREFIX)
app.include_router(scheduling.router,     prefix=PREFIX)
app.include_router(analytics.router,      prefix=PREFIX)
app.include_router(media.router,          prefix=PREFIX)
app.include_router(history.router,        prefix=PREFIX)
app.include_router(dashboard.router,      prefix=PREFIX)
app.include_router(chat.router,           prefix=PREFIX)
app.include_router(admin_usage.router,    prefix=PREFIX)
app.include_router(trends.router,         prefix=PREFIX)

logger.info("✅ All routers registered")


# ──────────────────────────────────────────────────────────
# STATIC STORAGE
# ──────────────────────────────────────────────────────────
BASE_STORAGE  = os.path.join(os.getcwd(), "storage")
MEDIA_STORAGE = os.path.join(BASE_STORAGE, "media")

os.makedirs(MEDIA_STORAGE, exist_ok=True)

app.mount("/media", StaticFiles(directory=MEDIA_STORAGE), name="media")

logger.info(f"📂 Media storage mounted: {MEDIA_STORAGE}")
