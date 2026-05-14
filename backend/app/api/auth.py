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

import logging
import secrets
import traceback
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, RedirectResponse

from app import db
from app.config import settings
from app.middleware.auth import encrypt_token, get_current_user
from app.middleware.pii import mask_email

logger = logging.getLogger(__name__)

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
OAUTH_NONCE_TTL_MINUTES = 10


def _frontend_redirect_url(path: str, query_params: dict[str, str] | None = None) -> str:
    """
    Build a frontend redirect URL from FRONTEND_URL and a relative path.

    This guarantees success/error redirects always use the same configured host
    and avoids trailing-slash mismatches in FRONTEND_URL.
    """
    base_url = settings.FRONTEND_URL.rstrip("/")
    normalized_path = path if path.startswith("/") else f"/{path}"
    if query_params:
        return f"{base_url}{normalized_path}?{urlencode(query_params)}"
    return f"{base_url}{normalized_path}"


def _oauth_error_redirect(error_code: str) -> RedirectResponse:
    return RedirectResponse(
        url=_frontend_redirect_url("/connect", {"error": error_code})
    )


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
    logger.info("[connect] step 1 — user authenticated: user_id=%s", user_id)

    # Always mint a fresh nonce to preserve one-time-use CSRF semantics.
    nonce = secrets.token_urlsafe(32)
    created_at = datetime.now(timezone.utc)
    expires_at = created_at + timedelta(minutes=OAUTH_NONCE_TTL_MINUTES)
    logger.info(
        "[connect] step 2 — fresh nonce generated for user_id=%s created_at=%s expires_at=%s ttl_minutes=%d",
        user_id,
        created_at.isoformat(),
        expires_at.isoformat(),
        OAUTH_NONCE_TTL_MINUTES,
    )

    try:
        await db.execute(
            """
            INSERT INTO oauth_nonces (user_id, nonce, expires_at, created_at)
            VALUES ($1, $2, $3, $4)
            """,
            user_id, nonce, expires_at, created_at,
        )
        logger.info("[connect] step 3 — nonce stored in oauth_nonces")
    except Exception as exc:
        logger.error("[connect] step 3 FAILED — could not insert nonce into oauth_nonces: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to store OAuth nonce. Check server logs.")

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
    logger.info("[connect] step 4 — returning auth_url to frontend (redirect_uri=%s)", settings.GOOGLE_REDIRECT_URI)
    return {"auth_url": auth_url}


# ---------------------------------------------------------------------------
# Step 2: handle the callback from Google
# ---------------------------------------------------------------------------

@router.get("/google/callback")
async def google_callback(
    code: str | None = Query(None),
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
        return _oauth_error_redirect("google_denied")

    if not code:
        logger.warning("[callback] missing authorization code in Google callback")
        return _oauth_error_redirect("missing_code")

    try:
        # Parse state: "<user_id>.<nonce>"
        try:
            user_id, nonce = state.split(".", 1)
        except ValueError:
            logger.warning("[callback] invalid OAuth state format: state=%s", state)
            raise HTTPException(status_code=400, detail="Invalid OAuth state parameter.")

        # Look up the nonce row. Keyed on the nonce itself so concurrent
        # attempts for the same user don't overwrite each other's state.
        nonce_row = await db.query_one(
            "SELECT user_id, expires_at, created_at FROM oauth_nonces WHERE nonce = $1",
            nonce,
        )
        if not nonce_row:
            logger.warning("[callback] nonce row not found nonce_prefix=%s", nonce[:8])
            raise HTTPException(status_code=400, detail="OAuth state not found. Please try connecting again.")

        # The state user_id must match the row's user_id so a valid nonce
        # can't be replayed against a different account.
        if str(nonce_row["user_id"]) != user_id:
            logger.warning("[callback] nonce user mismatch nonce_prefix=%s", nonce[:8])
            raise HTTPException(status_code=400, detail="OAuth state mismatch. Please retry Google connect.")

        expires_at = nonce_row["expires_at"]
        created_at = nonce_row.get("created_at")
        # asyncpg returns timezone-aware datetimes; ensure comparison is tz-aware
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if created_at and created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if now > expires_at:
            age_seconds = int((now - created_at).total_seconds()) if created_at else None
            logger.warning(
                "[callback] nonce expired for user_id=%s created_at=%s expires_at=%s callback_at=%s age_seconds=%s ttl_minutes=%d",
                user_id,
                created_at.isoformat() if created_at else None,
                expires_at.isoformat(),
                now.isoformat(),
                age_seconds,
                OAUTH_NONCE_TTL_MINUTES,
            )
            raise HTTPException(status_code=400, detail="OAuth state expired. Please try connecting again.")

        logger.info("[callback] nonce verified successfully for user_id=%s", user_id)

        # Consume the nonce (one-time use). Delete by nonce, not by user_id,
        # so a successful callback doesn't cancel a parallel in-flight attempt.
        await db.execute("DELETE FROM oauth_nonces WHERE nonce = $1", nonce)

        # Exchange code → tokens
        logger.info("[callback] exchanging code with Google (redirect_uri=%s)", settings.GOOGLE_REDIRECT_URI)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
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
        except httpx.HTTPError as exc:
            logger.warning("[callback] Google token exchange HTTP error: %s", exc)
            raise HTTPException(
                status_code=502,
                detail="Google token exchange failed to respond. Please try connecting again.",
            )

        logger.info("[callback] Google token exchange response: status=%d", token_response.status_code)

        if token_response.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail="Google token exchange failed. Please try connecting again.",
            )

        try:
            tokens = token_response.json()
        except Exception:
            logger.error("[callback] Google token exchange returned non-JSON response (status=%d)", token_response.status_code)
            raise HTTPException(
                status_code=502,
                detail="Google token exchange returned an unexpected response.",
            )
        access_token = tokens.get("access_token")
        if not access_token:
            raise HTTPException(
                status_code=502,
                detail="Google did not return an access token. Please try connecting again.",
            )
        refresh_token = tokens.get("refresh_token")
        expires_in = int(tokens.get("expires_in", 3600))
        logger.info("[callback] tokens received: has_access=%s has_refresh=%s expires_in=%s", bool(access_token), bool(refresh_token), expires_in)

        if not refresh_token:
            raise HTTPException(
                status_code=502,
                detail="Google did not return a refresh token. Try disconnecting and reconnecting.",
            )

        # Fetch the Google account email for display purposes
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
                userinfo_resp = await client.get(
                    GOOGLE_USERINFO_URL,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
        except httpx.HTTPError as exc:
            logger.warning("[callback] Google userinfo HTTP error: %s", exc)
            raise HTTPException(
                status_code=502,
                detail="Could not retrieve your Google userinfo. Please try connecting again.",
            )
        if userinfo_resp.status_code == 200:
            try:
                google_email = userinfo_resp.json().get("email", "")
            except Exception:
                google_email = ""
        else:
            google_email = ""
        logger.info("[callback] userinfo response: status=%d email=%s", userinfo_resp.status_code, mask_email(google_email))
        if not google_email:
            raise HTTPException(
                status_code=502,
                detail="Could not retrieve your Google email address. Please try connecting again.",
            )

        # Compute expiry timestamp
        token_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        if not isinstance(token_expiry, datetime):
            raise HTTPException(status_code=502, detail="Invalid token expiry returned by Google OAuth.")

        # Encrypt + persist — upsert so reconnecting overwrites existing row
        logger.info("[callback] storing credentials for user_id=%s google_email=%s", user_id, mask_email(google_email))
        await db.upsert(
            "google_connections",
            {
                "user_id": user_id,
                "google_email": google_email,
                "access_token": encrypt_token(access_token),
                "refresh_token": encrypt_token(refresh_token),
                "token_expiry": token_expiry,
                "connected_at": datetime.now(timezone.utc),
            },
            conflict_columns=["user_id"],
        )

        logger.info("[callback] success — redirecting user_id=%s to /dashboard", user_id)
        return RedirectResponse(url=_frontend_redirect_url("/dashboard"))

    except HTTPException as exc:
        logger.warning("[callback] handled HTTPException status=%s detail=%s", exc.status_code, exc.detail)
        # Map to a fixed set of user-facing reason codes — never expose raw
        # exception detail in the redirect URL.
        detail_lower = str(exc.detail).lower()
        if "expired" in detail_lower:
            return _oauth_error_redirect("oauth_expired")
        elif "state" in detail_lower or "nonce" in detail_lower or "csrf" in detail_lower:
            return _oauth_error_redirect("oauth_invalid_state")
        elif "refresh" in detail_lower:
            reason_code = "missing_refresh_token"
        elif "token" in detail_lower or "exchange" in detail_lower:
            reason_code = "token_exchange_failed"
        elif "email" in detail_lower or "userinfo" in detail_lower:
            reason_code = "userinfo_failed"
        else:
            reason_code = "unknown_error"
        return _oauth_error_redirect(reason_code)
    except Exception as exc:
        logger.error("[callback] unexpected error: %s\n%s", exc, traceback.format_exc())
        raise


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
