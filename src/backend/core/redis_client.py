"""
Shared Redis clients.

Auth routes previously called `aioredis.from_url(...)` on every /refresh and
/logout request, creating a fresh connection pool per request and closing it with
the deprecated `.close()`. These module-level clients are created once per
process and reuse their pools.
"""

import logging

import redis.asyncio as aioredis

from backend.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_general: aioredis.Redis | None = None
_pubsub: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    """Client for the general-purpose DB (blocklists, rate limits, breakers)."""
    global _general
    if _general is None:
        _general = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _general


def get_pubsub_redis() -> aioredis.Redis:
    """Client for the pub/sub DB (event stream, stream tickets)."""
    global _pubsub
    if _pubsub is None:
        _pubsub = aioredis.from_url(settings.REDIS_PUBSUB_URL, decode_responses=True)
    return _pubsub


async def close_redis_clients() -> None:
    global _general, _pubsub
    for client in (_general, _pubsub):
        if client is not None:
            try:
                await client.aclose()
            except Exception as e:
                logger.warning(f"Error closing Redis client: {e}")
    _general = None
    _pubsub = None
