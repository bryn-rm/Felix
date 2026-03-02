"""
Google API OAuth flow — Phase 1 core.

This is SEPARATE from the Supabase sign-in (handled by the frontend with the
Supabase JS client). These routes handle connecting a signed-in Felix user's
Google account for Gmail + Calendar API access.

Flow:
  1. Frontend calls GET /auth/google/connect (with Supabase JWT)
  2. Backend returns a Google OAuth URL
  3. User approves → Google redirects to GET /auth/google/callback
  4. Backend exchanges code for tokens, encrypts + stores in google_connections
  5. Redirects to /dashboard
"""

import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, RedirectResponse

from app import db
from app.config import settings
from app.middleware.auth import encrypt_token, get_current_user

router = APIRouter()

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "openid",
    "email",
    "profile",
]

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


# ---------------------------------------------------------------------------
# Step 1: initiate Google OAuth
# ---------------------------------------------------------------------------

@router.get("/google/connect")
async def connect_google(current_user: dict = Depends(get_current_user)):
    """
    Return the Google OAuth consent URL for the signed-in Felix user.
    The frontend should redirect the user to this URL.

    State parameter format: "<user_id>.<nonce>"
    The nonce is stored in oauth_nonces table with a 10-minute TTL and
    verified on callback to prevent CSRF attacks.
    """
    user_id = current_user["id"]

    # Generate a cryptographically random CSRF nonce
    nonce = secrets.token_urlsafe(32)

    # Store nonce with expiry (10 minutes) — scoped to this user
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    await db.execute(
        """
        INSERT INTO oauth_nonces (user_id, nonce, expires_at)
        VALUES ($1, $2, $3)
        ON CONFLICT (user_id) DO UPDATE SET nonce = $2, expires_at = $3
        """,
        user_id, nonce, expires_at,
    )

    # Encode both user_id and nonce in state so callback can verify
    state = f"{user_id}.{nonce}"

    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
    }

    auth_url = f"{GOOGLE_AUTH_URL}?{urlencode(params)}"
    return {"auth_url": auth_url}


# ---------------------------------------------------------------------------
# Step 2: handle the callback from Google
# ---------------------------------------------------------------------------

@router.get("/google/callback")
async def google_callback(
    code: str = Query(...),
    state: str = Query(...),
    error: str = Query(None),
):
    """
    Google redirects here after the user approves (or denies) access.
    Exchanges the code for tokens and stores them encrypted in google_connections.

    State format: "<user_id>.<nonce>" — nonce is verified against oauth_nonces
    to prevent CSRF attacks, then deleted (one-time use).
    """
    if error:
        return RedirectResponse(
            url=f"{settings.FRONTEND_URL}/settings?google_error={error}"
        )

    # Parse state: "<user_id>.<nonce>"
    try:
        user_id, nonce = state.split(".", 1)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid OAuth state parameter.")

    # Verify nonce — must exist, belong to this user, and not be expired
    nonce_row = await db.query_one(
        "SELECT nonce, expires_at FROM oauth_nonces WHERE user_id = $1",
        user_id,
    )
    if not nonce_row:
        raise HTTPException(status_code=400, detail="OAuth state not found. Please try connecting again.")

    if nonce_row["nonce"] != nonce:
        raise HTTPException(status_code=400, detail="OAuth state mismatch. Possible CSRF attack.")

    expires_at = nonce_row["expires_at"]
    # asyncpg returns timezone-aware datetimes; ensure comparison is tz-aware
    if expires_at.tzinfo is None:
        from datetime import timezone as _tz
        expires_at = expires_at.replace(tzinfo=_tz.utc)
    if datetime.now(timezone.utc) > expires_at:
        raise HTTPException(status_code=400, detail="OAuth state expired. Please try connecting again.")

    # Consume the nonce — one-time use
    await db.execute("DELETE FROM oauth_nonces WHERE user_id = $1", user_id)

    # Exchange code → tokens
    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri": settings.GOOGLE_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
        )

    if token_response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Google token exchange failed: {token_response.text}",
        )

    tokens = token_response.json()
    access_token = tokens["access_token"]
    refresh_token = tokens.get("refresh_token")
    expires_in = tokens.get("expires_in", 3600)

    if not refresh_token:
        # This shouldn't happen when prompt=consent, but guard anyway
        raise HTTPException(
            status_code=502,
            detail="Google did not return a refresh token. Try disconnecting and reconnecting.",
        )

    # Fetch the Google account email for display purposes
    async with httpx.AsyncClient() as client:
        userinfo_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    google_email = userinfo_resp.json().get("email", "") if userinfo_resp.status_code == 200 else ""

    # Compute expiry timestamp
    token_expiry = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()

    # Encrypt + persist — upsert so reconnecting overwrites existing row
    await db.upsert(
        "google_connections",
        {
            "user_id": user_id,
            "google_email": google_email,
            "access_token": encrypt_token(access_token),
            "refresh_token": encrypt_token(refresh_token),
            "token_expiry": token_expiry,
            "connected_at": datetime.now(timezone.utc).isoformat(),
        },
        conflict_columns=["user_id"],
    )

    return RedirectResponse(url=f"{settings.FRONTEND_URL}/onboarding")


# ---------------------------------------------------------------------------
# Check connection status
# ---------------------------------------------------------------------------

@router.get("/google/status")
async def google_connection_status(current_user: dict = Depends(get_current_user)):
    """Return whether this user has a connected Google account."""
    row = await db.query_one(
        "SELECT google_email, connected_at, last_sync FROM google_connections WHERE user_id = $1",
        current_user["id"],
    )
    if not row:
        return {"connected": False}
    return {
        "connected": True,
        "google_email": row["google_email"],
        "connected_at": row["connected_at"],
        "last_sync": row["last_sync"],
    }


# ---------------------------------------------------------------------------
# Disconnect Google account
# ---------------------------------------------------------------------------

@router.delete("/google/disconnect")
async def disconnect_google(current_user: dict = Depends(get_current_user)):
    """Remove stored Google credentials for this user."""
    await db.execute(
        "DELETE FROM google_connections WHERE user_id = $1", current_user["id"]
    )
    return {"disconnected": True}
