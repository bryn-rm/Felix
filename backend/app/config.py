from pydantic import AliasChoices, Field, model_validator
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
    ANTHROPIC_MODEL_SMART: str = "claude-sonnet-5"
    OPENAI_API_KEY: str = ""
    AI_MODEL_FAST: str = Field(
        default="gpt-5.6-luna",
        validation_alias=AliasChoices("AI_MODEL_FAST", "ANTHROPIC_MODEL_FAST"),
    )

    @model_validator(mode="before")
    @classmethod
    def discard_shadowed_fast_alias(cls, values):
        # Env + dotenv can supply both aliases. Pydantic otherwise treats the
        # unused legacy key as an extra setting and rejects startup.
        if isinstance(values, dict) and "AI_MODEL_FAST" in values and "ANTHROPIC_MODEL_FAST" in values:
            values = dict(values)
            values.pop("ANTHROPIC_MODEL_FAST")
        return values

    @model_validator(mode="after")
    def validate_ai_provider(self):
        if not self.ANTHROPIC_API_KEY.strip():
            raise ValueError("ANTHROPIC_API_KEY is required for the smart model")
        if self.AI_MODEL_FAST.startswith("gpt-5.6-luna"):
            if not self.OPENAI_API_KEY.strip():
                raise ValueError("OPENAI_API_KEY is required when AI_MODEL_FAST uses Luna")
        elif not self.AI_MODEL_FAST.startswith("claude-"):
            raise ValueError("AI_MODEL_FAST must be gpt-5.6-luna or a claude-* model")
        return self

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
