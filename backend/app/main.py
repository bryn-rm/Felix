"""
Felix backend — FastAPI entry point.

Start locally:
  uvicorn app.main:app --reload --port 8000

All routes require a valid Supabase JWT in the Authorization header, enforced
via Depends(get_current_user). The only exception is the health check.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import db
from app.api import auth, briefing, calendar, contacts, email, follow_ups, polish, settings, templates, voice
from app.api.eval import router as eval_router, admin_router
from app.config import settings as app_settings
from app.jobs.scheduler import scheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan: start/stop scheduler and DB pool
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Felix backend starting up")
    # Eagerly initialise the DB pool and verify connectivity at startup so a
    # misconfigured DATABASE_URL fails fast rather than on the first request.
    pool = await db.get_pool()
    await pool.fetchval("SELECT 1")
    logger.info("Database connection verified")
    scheduler.start()
    logger.info("APScheduler started")
    yield
    scheduler.shutdown()
    await db.close_pool()
    logger.info("Felix backend shut down")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Felix API",
    description="AI Email & Calendar Chief of Staff",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — only allow the configured frontend origin in production
_frontend_origin = app_settings.FRONTEND_URL.rstrip("/")
logger.info("CORS allow_origins: %r (raw FRONTEND_URL: %r)", [_frontend_origin], app_settings.FRONTEND_URL)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_frontend_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(auth.router,        prefix="/auth",        tags=["auth"])
app.include_router(email.router,       prefix="/emails",      tags=["email"])
app.include_router(calendar.router,    prefix="/calendar",    tags=["calendar"])
app.include_router(voice.router,       prefix="/voice",       tags=["voice"])
app.include_router(contacts.router,    prefix="/contacts",    tags=["contacts"])
app.include_router(follow_ups.router,  prefix="/follow-ups",  tags=["follow-ups"])
app.include_router(briefing.router,    prefix="/briefing",    tags=["briefing"])
app.include_router(polish.router,      prefix="/polish",      tags=["polish"])
app.include_router(settings.router,    prefix="/settings",    tags=["settings"])
app.include_router(templates.router,   prefix="/templates",   tags=["templates"])
app.include_router(eval_router,        prefix="/eval",         tags=["eval"])
app.include_router(admin_router,       prefix="/admin",        tags=["admin"])


# ---------------------------------------------------------------------------
# Health check — no auth required
# ---------------------------------------------------------------------------

@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok", "service": "felix-backend"}
