"""
Redis-backed sliding window rate limiter middleware for FastAPI.

Limits requests per IP address to prevent abuse. Uses Redis INCR + EXPIRE
for a simple fixed-window approach that's good enough for most use cases
and has near-zero overhead.

Configuration:
  - Auth endpoints (login/register): 10 requests per minute
  - General API endpoints: 60 requests per minute
  - Health check: unlimited (no rate limiting)
"""

import logging
import time

import redis.asyncio as aioredis
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from backend.core.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

# Rate limit tiers (requests per window)
RATE_LIMITS = {
    "auth": {"max_requests": 10, "window_seconds": 60},
    "default": {"max_requests": 60, "window_seconds": 60},
}

# Paths exempt from rate limiting
EXEMPT_PATHS = {"/health", "/docs", "/redoc", "/openapi.json"}


def _get_tier(path: str) -> str | None:
    """Determine rate limit tier based on request path."""
    if path in EXEMPT_PATHS:
        return None
    if "/auth/login" in path or "/auth/register" in path:
        return "auth"
    return "default"


def _get_client_ip(request: Request) -> str:
    """Extract client IP, respecting X-Forwarded-For behind a reverse proxy."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # Take the first IP (original client) from the chain
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Sliding window rate limiter using Redis INCR with TTL.

    Returns 429 Too Many Requests when limit is exceeded, with a
    Retry-After header indicating when the client can retry.
    """

    def __init__(self, app):
        super().__init__(app)
        self._redis = None

    async def _get_redis(self):
        if self._redis is None:
            self._redis = aioredis.from_url(
                settings.REDIS_URL, decode_responses=True
            )
        return self._redis

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        tier = _get_tier(request.url.path)

        # Skip rate limiting for exempt paths
        if tier is None:
            return await call_next(request)

        client_ip = _get_client_ip(request)
        limit_config = RATE_LIMITS[tier]
        max_requests = limit_config["max_requests"]
        window = limit_config["window_seconds"]

        # Build a key like "rl:default:192.168.1.1"
        key = f"rl:{tier}:{client_ip}"

        try:
            redis = await self._get_redis()
            current = await redis.incr(key)

            if current == 1:
                # First request in this window — set expiry
                await redis.expire(key, window)

            # Get remaining TTL for Retry-After header
            ttl = await redis.ttl(key)

            if current > max_requests:
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Too many requests. Please slow down.",
                        "retry_after_seconds": ttl if ttl > 0 else window,
                    },
                    headers={"Retry-After": str(ttl if ttl > 0 else window)},
                )

            response = await call_next(request)

            # Add informational rate limit headers
            response.headers["X-RateLimit-Limit"] = str(max_requests)
            response.headers["X-RateLimit-Remaining"] = str(
                max(0, max_requests - current)
            )
            response.headers["X-RateLimit-Reset"] = str(ttl if ttl > 0 else window)

            return response

        except Exception as e:
            # If Redis is down, fail open (allow the request through)
            logger.warning(f"Rate limiter Redis error (failing open): {e}")
            return await call_next(request)
