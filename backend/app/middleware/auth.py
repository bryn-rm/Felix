"""
Auth middleware for Felix.

Two concerns live here:
1. get_current_user — validates the Supabase JWT from the Authorization header
   and returns the authenticated user. Every FastAPI route uses this as a
   Depends() — no exceptions.

2. get_google_credentials — loads this user's stored Google OAuth tokens from
   google_connections, decrypts them, auto-refreshes if expired, and returns a
   google.oauth2.credentials.Credentials object ready to use.
"""

import asyncio
import base64
import hashlib
import logging
import time
from datetime import datetime, timezone

import jwt as pyjwt
from cryptography.fernet import Fernet
from fastapi import Depends, Header, HTTPException
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from supabase import Client, create_client

from app import db
from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Supabase client — service key only used here for JWT validation
# ---------------------------------------------------------------------------

_supabase: Client | None = None


def _get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        _supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
    return _supabase


# Public alias so other modules (e.g. voice.py) don't have to import a private name.
def get_supabase_client() -> Client:
    """Return the shared Supabase service-key client (singleton)."""
    return _get_supabase()


# ---------------------------------------------------------------------------
# Token encryption helpers
# ---------------------------------------------------------------------------

def _get_fernet() -> Fernet:
    """
    Build a Fernet cipher from TOKEN_ENCRYPTION_KEY.

    The env var is a 64-char hex string (from `openssl rand -hex 32`).
    Fernet needs a 32-byte URL-safe base64-encoded key, so we decode the hex
    bytes and re-encode them.
    """
    raw = bytes.fromhex(settings.TOKEN_ENCRYPTION_KEY)
    key = base64.urlsafe_b64encode(raw)
    return Fernet(key)


def encrypt_token(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    return _get_fernet().decrypt(ciphertext.encode()).decode()


# ---------------------------------------------------------------------------
# JWT validation — local verification with short-lived cache
# ---------------------------------------------------------------------------

# In-memory cache: token_hash → (user_dict, expiry_timestamp)
_jwt_cache: dict[str, tuple[dict, float]] = {}
_JWT_CACHE_TTL = 60  # seconds


def _cache_cleanup() -> None:
    """Evict expired entries (called lazily on each lookup)."""
    now = time.monotonic()
    expired = [k for k, (_, exp) in _jwt_cache.items() if now > exp]
    for k in expired:
        del _jwt_cache[k]


def _verify_jwt_locally(token: str) -> dict | None:
    """Verify a Supabase JWT using the shared secret.

    Returns the decoded payload on success, or None if the secret is not
    configured or the token fails verification.
    """
    secret = settings.SUPABASE_JWT_SECRET
    if not secret:
        return None
    try:
        payload = pyjwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
        return payload
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except pyjwt.InvalidTokenError:
        return None  # fall through to network validation


async def get_current_user(authorization: str = Header(...)) -> dict:
    """
    Validate the Supabase JWT sent as `Authorization: Bearer <token>`.

    Tries local signature verification first (fast, no network). Falls back to
    a Supabase network call if the JWT secret is not configured or if local
    verification fails for a non-expiry reason. Results are cached for 60s
    keyed on a hash of the token.

    Returns the authenticated user dict on success.
    Raises HTTP 401 on any failure.
    """
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing auth token")

    # Check cache first
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    _cache_cleanup()
    cached = _jwt_cache.get(token_hash)
    if cached:
        return cached[0].copy()

    # Try local verification
    payload = _verify_jwt_locally(token)
    if payload and payload.get("sub"):
        user = {
            "id": payload["sub"],
            "email": payload.get("email", ""),
            "metadata": payload.get("user_metadata", {}),
        }
        _jwt_cache[token_hash] = (user, time.monotonic() + _JWT_CACHE_TTL)
        return user

    # Fall back to Supabase network call
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_get_supabase().auth.get_user, token),
            timeout=5.0,
        )
        if not result or not result.user:
            raise HTTPException(status_code=401, detail="Invalid token")
        user = {
            "id": result.user.id,
            "email": result.user.email,
            "metadata": result.user.user_metadata or {},
        }
        _jwt_cache[token_hash] = (user, time.monotonic() + _JWT_CACHE_TTL)
        return user
    except HTTPException:
        raise
    except asyncio.TimeoutError:
        raise HTTPException(status_code=401, detail="Unauthorized")
    except Exception:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ---------------------------------------------------------------------------
# Google credential loading + auto-refresh
# ---------------------------------------------------------------------------

async def get_google_credentials(user_id: str) -> Credentials:
    """
    Load this user's Google OAuth tokens from google_connections, decrypt them,
    refresh if expired, persist the new token, and return Credentials.

    Raises HTTP 403 if the user hasn't connected their Google account yet.
    """
    row = await db.query_one(
        "SELECT * FROM google_connections WHERE user_id = $1", user_id
    )
    if not row:
        raise HTTPException(
            status_code=403,
            detail="Google account not connected. Visit /auth/google/connect to link it.",
        )

    # Parse stored expiry (TIMESTAMPTZ comes back as datetime from asyncpg, or as an
    # ISO string from older inserts). Pass it to Credentials so .expired works correctly.
    raw_expiry = row.get("token_expiry")
    if isinstance(raw_expiry, datetime):
        expiry = raw_expiry
    elif raw_expiry:
        try:
            expiry = datetime.fromisoformat(str(raw_expiry))
        except ValueError:
            logger.warning(
                "Malformed token_expiry for user %s: %r — treating as expired", user_id, raw_expiry
            )
            expiry = None
    else:
        expiry = None

    # google-auth's _helpers.utcnow() returns a NAIVE UTC datetime (tzinfo
    # stripped for backward compat). Credentials.expired compares expiry against
    # that, so expiry must also be naive UTC — otherwise we get "can't compare
    # offset-naive and offset-aware datetimes".
    if expiry is not None and expiry.tzinfo is not None:
        expiry = expiry.astimezone(timezone.utc).replace(tzinfo=None)

    creds = Credentials(
        token=decrypt_token(row["access_token"]),
        refresh_token=decrypt_token(row["refresh_token"]),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        expiry=expiry,
    )

    if creds.expired and creds.refresh_token:
        original_refresh_token = creds.refresh_token
        # creds.refresh() is a synchronous blocking HTTP call — run in a thread.
        try:
            await asyncio.to_thread(creds.refresh, Request())
        except Exception as exc:
            logger.warning(
                "Google token refresh failed for user %s: %s", user_id, exc
            )
            raise HTTPException(
                status_code=403,
                detail="Google access has expired. Please reconnect your account at /settings.",
            )
        update_data: dict = {
            "user_id": user_id,
            "access_token": encrypt_token(creds.token),
            "token_expiry": creds.expiry if creds.expiry else None,
        }
        # Google may rotate the refresh token; persist the new one if it changed.
        if creds.refresh_token and creds.refresh_token != original_refresh_token:
            update_data["refresh_token"] = encrypt_token(creds.refresh_token)
        await db.update("google_connections", update_data)

    return creds


# ---------------------------------------------------------------------------
# Convenience dependency: current user + their Google creds in one shot
# ---------------------------------------------------------------------------

async def get_current_user_with_google(
    current_user: dict = Depends(get_current_user),
) -> tuple[dict, Credentials]:
    """
    Combined dependency for routes that need both the user and Google creds.
    Usage: user, creds = Depends(get_current_user_with_google)
    """
    creds = await get_google_credentials(current_user["id"])
    return current_user, creds
