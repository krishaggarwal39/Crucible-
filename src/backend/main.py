import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sqlalchemy import text

from backend.api.deps import SessionDep
from backend.api.router import api_router
from backend.core.config import get_settings

settings = get_settings()

logging.basicConfig(level=logging.INFO if not settings.DEBUG else logging.DEBUG)
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    openapi_url="/api/v1/openapi.json",
)

# Set all CORS enabled origins
origins = settings.ALLOW_ORIGINS if settings.ALLOW_ORIGINS else ["http://localhost:3000"]
if not settings.ALLOW_ORIGINS and settings.APP_ENV != "development":
    logger.warning("CORS origins are empty in production. Configure ALLOW_ORIGINS.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")


@app.get("/health", tags=["Health"])
async def health_check(session: SessionDep):
    """
    Check if the API and Database are running.
    """
    try:
        await session.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Database connection failed")
        
    return {
        "status": "ok", 
        "db_status": db_status,
        "app_name": settings.APP_NAME
    }
