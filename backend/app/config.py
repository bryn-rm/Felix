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
    FELIX_VOICE_ID: str

    # Supabase
    SUPABASE_URL: str
    SUPABASE_SERVICE_KEY: str

    # Direct Postgres connection for background jobs (from Supabase project settings)
    # Format: postgresql://postgres.[ref]:[password]@aws-0-eu-west-2.pooler.supabase.com:6543/postgres
    DATABASE_URL: str

    # Encryption key for Google tokens at rest — generate with: openssl rand -hex 32
    TOKEN_ENCRYPTION_KEY: str

    # App
    BACKEND_SECRET_KEY: str
    FRONTEND_URL: str = "http://localhost:3000"

    class Config:
        env_file = ".env"


settings = Settings()
