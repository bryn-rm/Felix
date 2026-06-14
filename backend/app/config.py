from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Google OAuth (from GCP Console)
    GOOGLE_CLIENT_ID: str
    GOOGLE_CLIENT_SECRET: str
    GOOGLE_REDIRECT_URI: str

    # GCP
    GCP_PROJECT_ID: str
    GCP_REGION: str = "europe-west2"

    # AI
    ANTHROPIC_API_KEY: str
    ANTHROPIC_MODEL_SMART: str = "claude-sonnet-4-6"
    ANTHROPIC_MODEL_FAST: str = "claude-haiku-4-5-20251001"

    # ElevenLabs
    ELEVENLABS_API_KEY: str
    # Voice must support both eleven_flash_v2_5 (voice commands) and eleven_v3
    # (briefing audio). Most voices in the ElevenLabs library do; custom clones
    # may need verification in the ElevenLabs voice library settings.
    FELIX_VOICE_ID: str
    # JSON array of approved selectable voices, e.g.
    # [{"id":"21m00Tcm4TlvDq8ikWAM","label":"Rachel"}]
    FELIX_VOICE_CATALOG: str = ""

    # Supabase
    SUPABASE_URL: str
    SUPABASE_SERVICE_KEY: str
    SUPABASE_JWT_SECRET: str = ""  # For local JWT verification; falls back to network if empty

    # Direct Postgres connection for background jobs (from Supabase project settings)
    # Format: postgresql://postgres.[ref]:[password]@aws-0-eu-west-2.pooler.supabase.com:6543/postgres
    DATABASE_URL: str

    # Encryption key for Google tokens at rest — generate with: openssl rand -hex 32
    TOKEN_ENCRYPTION_KEY: str

    # App
    BACKEND_SECRET_KEY: str
    FRONTEND_URL: str = "http://localhost:3000"

    # Admin (optional — required to access /admin routes)
    # Comma-separated list of admin emails, e.g. "alice@co.com,bob@co.com"
    ADMIN_EMAILS: str = ""
    ADMIN_EMAIL: str = ""  # Deprecated — use ADMIN_EMAILS; kept for backward compat

    # Rate limiting — monthly AI call cap per user (0 = unlimited).
    # DEPRECATED: superseded by the unit-based caps below. Kept temporarily for
    # backward compatibility; no longer consulted by check_monthly_ai_budget.
    MONTHLY_AI_CALL_LIMIT: int = 5000
    # Higher cap applied when the caller's email matches ADMIN_EMAILS (0 = unlimited)
    ADMIN_MONTHLY_AI_CALL_LIMIT: int = 25000

    # AI quota — monthly billable-unit cap per user (0 = unlimited). Units are
    # cost-weighted (see _estimate_billable_units), not raw tokens or dollars.
    # Only interactive-scope calls are metered, so background triage/commitment
    # retries can't lock a user out of manual drafting/polishing.
    MONTHLY_AI_UNIT_LIMIT: float = 5_000_000
    # Higher cap applied when the caller's email matches ADMIN_EMAILS (0 = unlimited)
    ADMIN_MONTHLY_AI_UNIT_LIMIT: float = 0

    class Config:
        env_file = ".env"


settings = Settings()
