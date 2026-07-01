"""
Felix backend — FastAPI entry point.

Start locally:
  uvicorn app.main:app --reload --port 8000

All routes require a valid Supabase JWT in the Authorization header, enforced
via Depends(get_current_user). The only exception is the health check.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import db
from app.api import auth, briefing, calendar, commitments, contacts, email, follow_ups, jobs, meetings, meetings_ws, memory, polish, settings, templates, voice
from app.api.eval import router as eval_router, admin_router
from app.config import settings as app_settings
from app.errors import error_envelope
from app.jobs.scheduler import scheduler
from app.middleware.rate_limit import limiter, rate_limit_exceeded_handler

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
    try:
        from app.services.session_manager import flush_all_sessions

        flushed = await flush_all_sessions(reason="shutdown")
        if flushed:
            logger.info("Flushed %d active session(s) during shutdown", flushed)
    except Exception:
        logger.exception("Failed to flush active sessions during shutdown")
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

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)


# ---------------------------------------------------------------------------
# Unified error envelope: every non-2xx response is {"code", "message"}.
# Without these handlers we'd ship four shapes side-by-side:
#   - HTTPException        → {"detail": "..."}
#   - RequestValidationError → {"detail": [{loc, msg, ...}, ...]}
#   - SlowAPI 429          → {"detail": "Rate limit..."}
#   - bare 500             → {"detail": "Internal Server Error"}
# which forces every consumer to special-case the body shape.
# ---------------------------------------------------------------------------

async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail
    # exc.detail can be any JSON-serialisable value; coerce to a single string.
    if isinstance(detail, str):
        message = detail
    elif detail is None:
        message = "Error"
    else:
        message = str(detail)
    return JSONResponse(
        status_code=exc.status_code,
        content=error_envelope(exc.status_code, message),
        headers=getattr(exc, "headers", None),
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    errors = exc.errors()
    if errors:
        first = errors[0]
        loc = ".".join(str(p) for p in first.get("loc", []) if p != "body") or "body"
        message = f"{loc}: {first.get('msg', 'Invalid value')}"
    else:
        message = "Invalid request"
    return JSONResponse(
        status_code=422,
        content=error_envelope(422, message, code="validation_error"),
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # Catches anything not already matched (DB outages, KeyError, asyncio bugs…).
    # Log with traceback for debugging; return a generic envelope so we don't
    # leak internals (connection strings, file paths) to the client.
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content=error_envelope(500, "Internal server error"),
    )


app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)

# CORS — only allow the configured frontend origin in production
_frontend_origin = app_settings.FRONTEND_URL.rstrip("/")
logger.info("CORS allow_origins: %r (raw FRONTEND_URL: %r)", [_frontend_origin], app_settings.FRONTEND_URL)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_frontend_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Requested-With"],
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
app.include_router(memory.router,      prefix="/memory",      tags=["memory"])
app.include_router(meetings.router,    prefix="/meetings",    tags=["meetings"])
app.include_router(meetings_ws.router,                         tags=["meetings"])  # /ws/meetings/{id} — no REST prefix
app.include_router(commitments.router, prefix="/commitments", tags=["commitments"])
app.include_router(jobs.router,        prefix="/jobs",        tags=["jobs"])
app.include_router(eval_router,        prefix="/eval",         tags=["eval"])
app.include_router(admin_router,       prefix="/admin",        tags=["admin"])


# ---------------------------------------------------------------------------
# Health check — no auth required
# ---------------------------------------------------------------------------

@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok", "service": "felix-backend"}
