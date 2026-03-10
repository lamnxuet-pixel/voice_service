"""Application configuration via Pydantic Settings."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/voice_patient_registration"

    # Google Gemini
    gemini_api_key: str = ""

    # Deepgram (for Speech-to-Text) - Free tier: 200 min/month
    deepgram_api_key: str = ""

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    # Workflow Engine Mode
    # "standard" = Standard workflow with caching (default, faster)
    # "advanced" = Advanced n8n-style workflow with detailed tracking (more features)
    workflow_mode: str = "standard"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
