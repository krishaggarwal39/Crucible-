from functools import lru_cache
from typing import Annotated

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _split_csv(value):
    """
    Allow list-typed settings to be supplied as a comma-separated string.

    pydantic-settings only accepts JSON for complex types by default, so
    `ALLOW_ORIGINS=http://a.com,http://b.com` would otherwise raise a parse
    error, and `ALLOW_ORIGINS=` (empty, as emitted by docker-compose when the
    variable is unset) would crash the app at startup.
    """
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        # Preserve JSON-style input for backwards compatibility. NoDecode means
        # pydantic-settings no longer decodes it for us, so do it here.
        if stripped.startswith("["):
            import json

            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                pass
        return [part.strip() for part in stripped.split(",") if part.strip()]
    return value


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
    # NoDecode: skip pydantic-settings' JSON decoding so the raw env string
    # reaches _parse_csv_lists below. Without it, `ALLOW_ORIGINS=a,b` and even
    # `ALLOW_ORIGINS=` (what compose emits for an unset var) crash at startup.
    ALLOW_ORIGINS: Annotated[list[str], NoDecode] = []

    # Number of trusted reverse proxies in front of the app.
    # 0  -> never trust X-Forwarded-For (correct when the app is exposed directly)
    # 1  -> exactly one trusted proxy (e.g. the bundled nginx)
    # The rate limiter uses this to pick the correct hop from the XFF chain, so a
    # client cannot choose its own rate-limit identity by forging the header.
    #
    # IMPORTANT: run uvicorn with --no-proxy-headers. Uvicorn's proxy-header
    # handling defaults to ON and rewrites request.client from X-Forwarded-For
    # before any application code sees it, which would silently override this
    # setting. With --forwarded-allow-ips='*' uvicorn additionally takes the
    # leftmost (client-supplied) hop, which is directly forgeable.
    TRUSTED_PROXY_COUNT: int = 0

    @property
    def IS_PRODUCTION(self) -> bool:
        return self.APP_ENV.lower() not in ("development", "dev", "local", "test")

    @property
    def COOKIE_SECURE(self) -> bool:
        """
        Whether to set the Secure flag on auth cookies.

        This is a real property so callers cannot silently read a misspelled
        attribute and always get a falsy default (which is what previously kept
        the refresh cookie non-Secure in every environment).
        """
        return self.IS_PRODUCTION

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
    # Hard ceiling for a single evaluation run, enforced by Celery itself so a
    # wedged run cannot outlive the zombie sweeper.
    TASK_SOFT_TIME_LIMIT_SECONDS: int = 3600
    TASK_TIME_LIMIT_SECONDS: int = 3900
    # Max concurrent in-flight LLM/target calls inside a single graph node.
    NODE_CONCURRENCY_LIMIT: int = 10

    # ── Qdrant ───────────────────────────────────────────────────────────────
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_API_KEY: str = ""
    QDRANT_COLLECTION: str = "behavioral_traces"
    # Page size used when scrolling baseline vectors out of Qdrant.
    QDRANT_SCROLL_PAGE_SIZE: int = 256

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
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ── Encryption ───────────────────────────────────────────────────────────
    # A base64-encoded 32-byte key generated via `Fernet.generate_key()`
    CREDENTIAL_ENCRYPTION_KEY: str

    # ── Security ─────────────────────────────────────────────────────────────
    # None -> derive from APP_ENV (on outside development).
    # Set explicitly to true to exercise SSRF protection locally.
    SSRF_PROTECTION_ENABLED: bool | None = None

    @property
    def ssrf_protection_active(self) -> bool:
        if self.SSRF_PROTECTION_ENABLED is not None:
            return self.SSRF_PROTECTION_ENABLED
        return self.IS_PRODUCTION

    @field_validator("ALLOW_ORIGINS", "FALLBACK_MODELS", mode="before")
    @classmethod
    def _parse_csv_lists(cls, value):
        return _split_csv(value)

    @model_validator(mode="after")
    def validate_jwt_secret(self) -> "Settings":
        if self.IS_PRODUCTION and self.JWT_SECRET_KEY == "super_secret_jwt_key_change_in_production":
            raise ValueError("JWT_SECRET_KEY must be overridden in non-development environments.")
        return self

    @model_validator(mode="after")
    def validate_encryption_key(self) -> "Settings":
        """
        Fail fast on an unusable Fernet key.

        Previously an invalid key was silently padded/truncated and re-encoded,
        which produced a *different working key*. That silently rendered every
        previously stored credential undecryptable with no error anywhere.
        """
        from cryptography.fernet import Fernet

        try:
            Fernet(self.CREDENTIAL_ENCRYPTION_KEY.encode("utf-8"))
        except Exception as exc:
            raise ValueError(
                "CREDENTIAL_ENCRYPTION_KEY is not a valid Fernet key. "
                "Generate one with: python -c "
                "\"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            ) from exc
        return self

    # Create the S3/MinIO bucket at API startup if it is missing.
    INIT_STORAGE_ON_STARTUP: bool = True

    # ── Observability ────────────────────────────────────────────────────────
    # Off by default: the console exporters are very noisy in local development.
    OTEL_ENABLED: bool = False
    OTEL_SERVICE_NAME: str = "crucible"
    # When set, traces/metrics go to this OTLP HTTP collector instead of stdout.
    OTEL_EXPORTER_OTLP_ENDPOINT: str = ""

    # ── AI Providers ─────────────────────────────────────────────────────────
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    GROQ_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""

    # ── LLM Defaults ─────────────────────────────────────────────────────────
    DEFAULT_JUDGE_MODEL: str = "openrouter/nvidia/nemotron-3-super-120b-a12b:free"
    DEFAULT_GENERATOR_MODEL: str = "openrouter/nvidia/nemotron-3-super-120b-a12b:free"
    # Must be a real *embedding* model. A chat model here makes every drift
    # analysis fail, because litellm.aembedding() cannot serve it.
    # gemini/text-embedding-004 emits 768 dimensions, matching EMBEDDING_DIMENSIONS.
    DEFAULT_EMBEDDING_MODEL: str = "gemini/text-embedding-004"
    EMBEDDING_DIMENSIONS: int = 768
    FALLBACK_MODELS: Annotated[list[str], NoDecode] = [
        "openrouter/nvidia/nemotron-3-nano-30b-a3b:free",
        "openrouter/openai/gpt-oss-20b:free",
    ]
    MAX_TOKENS_JUDGE: int = 4096
    MAX_TOKENS_GENERATOR: int = 8192


@lru_cache
def get_settings() -> Settings:
    """Cached singleton. Import this everywhere instead of Settings()."""
    return Settings()
