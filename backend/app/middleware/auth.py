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
from datetime import datetime, timezone

from cryptography.fernet import Fernet
from fastapi import Depends, Header, HTTPException
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from supabase import Client, create_client

from app import db
from app.config import settings

# ---------------------------------------------------------------------------
# Supabase client — service key only used here for JWT validation
# ---------------------------------------------------------------------------

_supabase: Client | None = None


def _get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        _supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
    return _supabase


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
# JWT validation
# ---------------------------------------------------------------------------

async def get_current_user(authorization: str = Header(...)) -> dict:
    """
    Validate the Supabase JWT sent as `Authorization: Bearer <token>`.

    Returns the Supabase user object dict on success.
    Raises HTTP 401 on any failure.
    """
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing auth token")

    try:
        result = _get_supabase().auth.get_user(token)
        if not result or not result.user:
            raise HTTPException(status_code=401, detail="Invalid token")
        return {
            "id": result.user.id,
            "email": result.user.email,
            "metadata": result.user.user_metadata or {},
        }
    except HTTPException:
        raise
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
        expiry = datetime.fromisoformat(str(raw_expiry))
    else:
        expiry = None

    creds = Credentials(
        token=decrypt_token(row["access_token"]),
        refresh_token=decrypt_token(row["refresh_token"]),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        expiry=expiry,
    )

    if creds.expired and creds.refresh_token:
        # creds.refresh() is a synchronous blocking HTTP call — run in a thread.
        await asyncio.to_thread(creds.refresh, Request())
        await db.update(
            "google_connections",
            {
                "user_id": user_id,
                "access_token": encrypt_token(creds.token),
                "token_expiry": creds.expiry.isoformat() if creds.expiry else None,
            },
        )

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
