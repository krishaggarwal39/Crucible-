"""
Redis-backed fixed-window rate limiter middleware for FastAPI.

Limits requests per IP address to prevent abuse. Uses Redis INCR + EXPIRE
for a simple fixed-window approach that's good enough for most use cases
and has near-zero overhead.

Note: this is a FIXED window, not a sliding window. Counters reset at window
boundaries, so a client can send up to 2x max_requests across the boundary of
two adjacent windows. That burst is acceptable for this limiter's purpose.

Configuration:
  - Auth endpoints (login/register): 10 requests per minute
  - General API endpoints: 60 requests per minute
  - Health check: unlimited (no rate limiting)
"""

import asyncio
import logging

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

# Paths exempt from rate limiting.
# Note the API's real OpenAPI path is /api/v1/openapi.json — listing a bare
# "/openapi.json" exempted a route that does not exist, so the schema endpoint
# was silently consuming the default tier.
EXEMPT_PATHS = {
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/v1/openapi.json",
}


def _get_tier(path: str) -> str | None:
    """Determine rate limit tier based on request path."""
    if path in EXEMPT_PATHS:
        return None
    if "/auth/login" in path or "/auth/register" in path:
        return "auth"
    return "default"


def _get_client_ip(request: Request) -> str:
    """
    Determine the client identity used for rate limiting.

    X-Forwarded-For is only consulted when TRUSTED_PROXY_COUNT > 0, and then we
    take the hop *our* trusted proxy appended rather than the first entry.

    Taking the first entry unconditionally let any caller choose its own
    rate-limit bucket by sending a forged header — verified as a complete bypass
    of the login limiter. nginx appends rather than replaces, so the leftmost
    value is attacker-controlled even behind the real proxy.
    """
    trusted = settings.TRUSTED_PROXY_COUNT
    if trusted > 0:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            hops = [h.strip() for h in forwarded.split(",") if h.strip()]
            if hops:
                # The rightmost `trusted` entries were added by infrastructure we
                # control; the one just before them is the real peer.
                index = max(0, len(hops) - trusted)
                return hops[index]
    return request.client.host if request.client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Fixed-window rate limiter using Redis INCR with TTL.

    Returns 429 Too Many Requests when limit is exceeded, with a
    Retry-After header indicating when the client can retry.
    """

    def __init__(self, app):
        super().__init__(app)
        self._redis = None
        self._redis_loop = None

    async def _get_redis(self):
        # A redis.asyncio client is bound to the event loop that created it.
        # Cache it, but recreate it if the running loop has changed (e.g. the
        # loop it was built on was closed) — otherwise a stale client raises
        # "Event loop is closed" and the auth tier would fail closed on a
        # perfectly healthy Redis.
        loop = asyncio.get_running_loop()
        if self._redis is None or self._redis_loop is not loop:
            self._redis = aioredis.from_url(
                settings.REDIS_URL, decode_responses=True
            )
            self._redis_loop = loop
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

        # Only the Redis counter operations are guarded here. The downstream
        # request (call_next) is deliberately OUTSIDE this try: an exception from
        # a route handler must not be mistaken for a Redis outage and converted
        # into a rate-limit decision.
        try:
            redis = await self._get_redis()
            current = await redis.incr(key)

            if current == 1:
                # First request in this window — set expiry
                await redis.expire(key, window)

            # Get remaining TTL for Retry-After header
            ttl = await redis.ttl(key)
        except Exception as e:
            # Redis is the only brute-force control on the auth endpoints, so a
            # Redis outage must NOT silently remove login/register throttling.
            # Fail CLOSED for the "auth" tier (deny with 503); keep fail-open for
            # the "default" tier so a Redis blip does not take down the whole API.
            if tier == "auth":
                logger.warning(
                    f"Rate limiter Redis error (failing closed for auth tier): {e}"
                )
                return JSONResponse(
                    status_code=503,
                    content={
                        "detail": "Service temporarily unavailable. Please retry shortly.",
                        "retry_after_seconds": window,
                    },
                    headers={"Retry-After": str(window)},
                )
            logger.warning(f"Rate limiter Redis error (failing open): {e}")
            return await call_next(request)

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
        response.headers["X-RateLimit-Remaining"] = str(max(0, max_requests - current))
        response.headers["X-RateLimit-Reset"] = str(ttl if ttl > 0 else window)

        return response
