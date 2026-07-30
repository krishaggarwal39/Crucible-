from functools import lru_cache
from typing import Annotated

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


# Provider prefix -> the settings field holding its API key. Used to decide
# whether drift analysis is actually usable before the pipeline tries it.
_EMBEDDING_PROVIDER_KEYS = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "cohere": "COHERE_API_KEY",
    "voyage": "VOYAGE_API_KEY",
}

# Substrings that indicate a placeholder rather than a real credential.
_PLACEHOLDER_MARKERS = ("your_", "_here", "changeme", "xxxx", "replace_me")


def _is_real_secret(value: object) -> bool:
    """True only for a non-empty value that is not an obvious placeholder."""
    if not isinstance(value, str):
        return False
    v = value.strip()
    if not v:
        return False
    lowered = v.lower()
    return not any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


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
    # OPENROUTER_API_KEY is the only one the default configuration needs: it
    # serves the judge, generator and evolution models.
    OPENROUTER_API_KEY: str = ""
    # GEMINI_API_KEY is needed only for drift analysis (embeddings). Without it
    # the pipeline still runs; drift is reported as disabled.
    GEMINI_API_KEY: str = ""
    # Optional alternative providers. Nothing routes to these by default.
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    GROQ_API_KEY: str = ""
    MISTRAL_API_KEY: str = ""
    COHERE_API_KEY: str = ""
    VOYAGE_API_KEY: str = ""

    # ── LLM Defaults ─────────────────────────────────────────────────────────
    # Chat/structured-output models. All of these are OpenRouter free tier and were
    # verified to work with Instructor's default TOOLS mode, which is what the
    # judge, generator and evolution nodes rely on.
    DEFAULT_JUDGE_MODEL: str = "openrouter/nvidia/nemotron-3-super-120b-a12b:free"
    DEFAULT_GENERATOR_MODEL: str = "openrouter/nvidia/nemotron-3-super-120b-a12b:free"

    # Fallbacks are tried by litellm when the primary model fails.
    #
    # Deliberately excludes openrouter/openai/gpt-oss-20b:free — it returns a
    # provider error under TOOLS mode, so having it here turned a transient
    # primary failure into a hard failure. Both entries below were verified to
    # produce valid structured output in TOOLS mode.
    FALLBACK_MODELS: Annotated[list[str], NoDecode] = [
        "openrouter/nvidia/nemotron-3-nano-30b-a3b:free",
        "openrouter/nvidia/nemotron-nano-9b-v2:free",
    ]

    MAX_TOKENS_JUDGE: int = 4096
    MAX_TOKENS_GENERATOR: int = 8192

    # ── Embeddings (drift analysis only) ─────────────────────────────────────
    # Must be a real *embedding* model. A chat model here breaks every drift
    # analysis, because litellm.aembedding() cannot serve one.
    #
    # OpenRouter has NO embeddings endpoint — it is chat-completions only — so an
    # `openrouter/...` value can never work here. validate_embedding_model below
    # rejects that outright rather than letting it fail per-trace at runtime.
    #
    # gemini/gemini-embedding-001 natively returns 3072 dimensions but accepts a
    # `dimensions` argument, so EMBEDDING_DIMENSIONS controls the real output
    # size and the Qdrant collection stays consistent.
    #
    # Leave DEFAULT_EMBEDDING_MODEL empty to disable drift analysis entirely; the
    # rest of the pipeline (generate, simulate, judge, evolve) is unaffected.
    DEFAULT_EMBEDDING_MODEL: str = "gemini/gemini-embedding-001"
    EMBEDDING_DIMENSIONS: int = 768

    @property
    def embedding_provider(self) -> str:
        """Provider prefix of the configured embedding model (e.g. 'gemini')."""
        model = (self.DEFAULT_EMBEDDING_MODEL or "").strip()
        return model.split("/", 1)[0] if "/" in model else ""

    @property
    def embeddings_enabled(self) -> bool:
        """
        Whether drift analysis can actually run.

        False when no embedding model is configured, or when the API key its
        provider needs is missing or still a placeholder. The analyzer checks this
        and reports a clear reason instead of failing once per trace.
        """
        if not (self.DEFAULT_EMBEDDING_MODEL or "").strip():
            return False
        key_field = _EMBEDDING_PROVIDER_KEYS.get(self.embedding_provider)
        if key_field is None:
            # Unknown provider: assume the operator configured it deliberately.
            return True
        return _is_real_secret(getattr(self, key_field, ""))

    @property
    def embeddings_disabled_reason(self) -> str | None:
        if self.embeddings_enabled:
            return None
        if not (self.DEFAULT_EMBEDDING_MODEL or "").strip():
            return "No embedding model is configured (DEFAULT_EMBEDDING_MODEL is empty)."
        key_field = _EMBEDDING_PROVIDER_KEYS.get(self.embedding_provider, "the provider API key")
        return (
            f"{key_field} is not set, so {self.DEFAULT_EMBEDDING_MODEL!r} cannot be used."
        )

    @model_validator(mode="after")
    def validate_embedding_model(self) -> "Settings":
        if self.embedding_provider == "openrouter":
            raise ValueError(
                "DEFAULT_EMBEDDING_MODEL cannot be an OpenRouter model: OpenRouter "
                "serves chat completions only and has no embeddings endpoint. Use an "
                "embedding provider (e.g. gemini/gemini-embedding-001), or leave it "
                "empty to disable drift analysis."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Cached singleton. Import this everywhere instead of Settings()."""
    return Settings()
