from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ──────────────────────────────────────────────────────────
    APP_NAME: str = "Crucible"
    APP_ENV: str = "development"
    DEBUG: bool = False
    ALLOW_ORIGINS: list[str] = []

    # ── Database ─────────────────────────────────────────────────────────────
    POSTGRES_USER: str = "crucible"
    POSTGRES_PASSWORD: str = "crucible_secret"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "crucible"
    DATABASE_URL: str = ""          # assembled below if not provided directly

    @model_validator(mode="after")
    def assemble_db_url(self) -> "Settings":
        if not self.DATABASE_URL:
            self.DATABASE_URL = (
                f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
                f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
            )
        return self

    # Sync URL for Alembic (uses psycopg2 / no asyncpg)
    @property
    def SYNC_DATABASE_URL(self) -> str:
        return self.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")

    # ── Redis ─────────────────────────────────────────────────────────────────
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_URL: str = ""

    @model_validator(mode="after")
    def assemble_redis_url(self) -> "Settings":
        if not self.REDIS_URL:
            self.REDIS_URL = f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/0"
        return self

    # ── Celery & Pub/Sub ─────────────────────────────────────────────────────
    CELERY_BROKER_URL: str = ""
    CELERY_RESULT_BACKEND: str = ""
    REDIS_PUBSUB_URL: str = ""

    @model_validator(mode="after")
    def assemble_celery_urls(self) -> "Settings":
        if not self.CELERY_BROKER_URL:
            self.CELERY_BROKER_URL = f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/0"
        if not self.CELERY_RESULT_BACKEND:
            self.CELERY_RESULT_BACKEND = f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/0"
        if not self.REDIS_PUBSUB_URL:
            self.REDIS_PUBSUB_URL = f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/1"
        return self

    # ── Orchestration & Timeouts ─────────────────────────────────────────────
    HEARTBEAT_INTERVAL_SECONDS: int = 30
    ZOMBIE_TIMEOUT_SECONDS: int = 300
    
    # ── Qdrant ───────────────────────────────────────────────────────────────
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_API_KEY: str = ""

    # ── S3 / Blob Storage ────────────────────────────────────────────────────
    # For MinIO local dev, S3_ENDPOINT_URL might be "http://localhost:9000"
    # For AWS S3, leave S3_ENDPOINT_URL empty. For R2, set to Cloudflare URL.
    S3_ENDPOINT_URL: str | None = "http://localhost:9000"
    S3_ACCESS_KEY: str = "admin"
    S3_SECRET_KEY: str = "admin_secret"
    S3_REGION: str = "us-east-1"
    S3_BUCKET_TRACES: str = "crucible-traces"
    S3_USE_SSL: bool = False

    # ── Auth ─────────────────────────────────────────────────────────────────
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 15  # 15 minutes

    # ── Encryption ───────────────────────────────────────────────────────────
    # A base64-encoded 32-byte key generated via `Fernet.generate_key()`
    CREDENTIAL_ENCRYPTION_KEY: str

    @model_validator(mode="after")
    def validate_jwt_secret(self) -> "Settings":
        if self.APP_ENV != "development" and self.JWT_SECRET_KEY == "super_secret_jwt_key_change_in_production":
            raise ValueError("JWT_SECRET_KEY must be overridden in non-development environments.")
        return self

    # ── AI Providers ─────────────────────────────────────────────────────────
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    GROQ_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""

    # ── LLM Defaults ─────────────────────────────────────────────────────────
    DEFAULT_JUDGE_MODEL: str = "openrouter/nvidia/nemotron-3-super-120b-a12b:free"
    DEFAULT_GENERATOR_MODEL: str = "openrouter/nvidia/nemotron-3-super-120b-a12b:free"
    DEFAULT_EMBEDDING_MODEL: str = "openrouter/nvidia/nemotron-3-super-120b-a12b:free"
    FALLBACK_MODELS: list[str] = ["openrouter/nvidia/nemotron-3-nano-30b-a3b:free", "openrouter/openai/gpt-oss-20b:free"]
    MAX_TOKENS_JUDGE: int = 4096
    MAX_TOKENS_GENERATOR: int = 8192


@lru_cache
def get_settings() -> Settings:
    """Cached singleton. Import this everywhere instead of Settings()."""
    return Settings()
