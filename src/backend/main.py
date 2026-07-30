import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from backend.api.deps import SessionDep
from backend.api.router import api_router
from backend.core.config import get_settings
from backend.core.rate_limit import RateLimitMiddleware
from backend.core.telemetry import instrument_fastapi, setup_telemetry

settings = get_settings()

logging.basicConfig(level=logging.INFO if not settings.DEBUG else logging.DEBUG)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup/shutdown wiring.

    setup_telemetry() is called here — it previously existed but was never
    invoked anywhere, so no metrics or traces were ever produced.
    """
    setup_telemetry()
    instrument_fastapi(app)

    # Ensure the trace bucket exists. initialize_bucket() was only ever called
    # from tests, so a fresh deployment had no bucket and every trace upload
    # failed after exhausting its retries.
    if settings.INIT_STORAGE_ON_STARTUP:
        try:
            from backend.connectors.s3 import S3BlobStore

            store = S3BlobStore()
            try:
                await store.initialize_bucket()
                logger.info("Verified trace bucket %s", settings.S3_BUCKET_TRACES)
            finally:
                await store.close()
        except Exception as e:
            # Non-fatal: the API can serve reads without object storage.
            logger.warning(f"Could not verify trace bucket at startup: {e}")

    yield


app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    openapi_url="/api/v1/openapi.json",
    lifespan=lifespan,
)

# Set all CORS enabled origins
origins = settings.ALLOW_ORIGINS if settings.ALLOW_ORIGINS else ["http://localhost:3000"]
if not settings.ALLOW_ORIGINS and settings.IS_PRODUCTION:
    logger.warning("CORS origins are empty in production. Configure ALLOW_ORIGINS.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting (must be added after CORS so CORS headers still get set on 429 responses)
app.add_middleware(RateLimitMiddleware)

app.include_router(api_router, prefix="/api/v1")


@app.get("/health", tags=["Health"])
async def health_check():
    """
    Liveness probe.

    Deliberately does NOT touch the database. This endpoint is unauthenticated
    and exempt from rate limiting, so querying Postgres here turned it into a
    free amplification vector for anonymous callers. Use /health/ready for a
    dependency check.
    """
    return {"status": "ok", "app_name": settings.APP_NAME}


@app.get("/health/ready", tags=["Health"])
async def readiness_check(session: SessionDep):
    """
    Readiness probe — verifies the database is reachable.

    Rate limited under the default tier because it does real work.
    """
    try:
        await session.execute(text("SELECT 1"))
    except Exception as e:
        logger.error(f"Readiness check failed: {e}")
        raise HTTPException(status_code=503, detail="Database connection failed")

    return {"status": "ok", "db_status": "ok", "app_name": settings.APP_NAME}
