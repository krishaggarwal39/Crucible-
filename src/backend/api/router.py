from fastapi import APIRouter

from backend.api.routes import auth, evaluation, agent_config

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(evaluation.router)
api_router.include_router(agent_config.router)
