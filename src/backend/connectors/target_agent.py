import ipaddress
import logging
import socket
from urllib.parse import urlparse
from typing import Any, Dict

import httpx
import httpcore
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from backend.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class SSRFViolationError(Exception):
    pass


class TargetAgentCircuitBreakerOpen(Exception):
    pass


class SSRFSafeBackend(httpcore.AnyIOBackend):
    """
    Custom network backend that enforces SSRF protection during DNS resolution.
    It resolves the hostname to an IP, validates the IP, and connects directly to the IP.
    This prevents TOCTOU (Time-of-Check to Time-of-Use) DNS rebinding attacks.
    """
    async def connect_tcp(self, host: str, port: int, timeout: float = None, local_address: str = None, **kwargs):
        try:
            # Resolve the hostname to an IP
            addr_info = socket.getaddrinfo(host, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
            # Find the first valid IP
            ip = addr_info[0][4][0]
            ip_obj = ipaddress.ip_address(ip)
            
            if settings.APP_ENV != "development":
                if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local:
                    raise SSRFViolationError(f"Resolved IP {ip} is in a restricted private range.")
                    
            # Connect using the validated IP to avoid TOCTOU
            return await super().connect_tcp(ip, port, timeout=timeout, local_address=local_address, **kwargs)
        except socket.gaierror:
            raise SSRFViolationError(f"Could not resolve hostname: {host}")


class CircuitBreaker:
    """Redis-backed Circuit Breaker to share state across distributed workers."""
    def __init__(self, key_prefix: str, failure_threshold: int = 4):
        self.failure_threshold = failure_threshold
        import redis.asyncio as redis
        self.redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
        self.key = f"circuit_breaker:{key_prefix}"
        
    async def record_failure(self):
        failures = await self.redis.incr(self.key)
        if failures == 1:
            # Expire failures after 60 seconds of no new failures
            await self.redis.expire(self.key, 60)
            
    async def record_success(self):
        await self.redis.delete(self.key)
        
    async def is_open(self) -> bool:
        failures_str = await self.redis.get(self.key)
        if failures_str:
            return int(failures_str) >= self.failure_threshold
        return False
        
    async def close(self):
        await self.redis.aclose()


class TargetAgentConnector:
    """
    Connects to an external Target Agent REST API to simulate evaluation interactions.
    Includes strict timeouts, SSRF protection (TOCTOU safe), connection pooling, 
    and a Redis-backed Circuit Breaker.
    """
    def __init__(self, endpoint_url: str, bearer_token: str | None = None):
        parsed = urlparse(endpoint_url)
        if settings.APP_ENV != "development":
            if parsed.scheme != "https":
                raise SSRFViolationError("Only HTTPS URLs are allowed in production.")
                
        self.endpoint_url = endpoint_url
        self.bearer_token = bearer_token
        # 5 second connect, 30 second read
        self.timeout = httpx.Timeout(30.0, connect=5.0)
        
        # Redis-backed Circuit Breaker
        self.circuit_breaker = CircuitBreaker(
            key_prefix=parsed.hostname, 
            failure_threshold=4
        )
        
        # Setup SSRF safe transport and connection pooling
        transport = httpx.AsyncHTTPTransport()
        transport._pool._network_backend = SSRFSafeBackend()
        self.client = httpx.AsyncClient(timeout=self.timeout, transport=transport)
        
    async def close(self):
        await self.client.aclose()
        await self.circuit_breaker.close()
        
    @retry(
        retry=retry_if_exception_type(httpx.RequestError),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(3),
        reraise=True
    )
    async def _send_with_retry(self, headers: Dict[str, str], payload: Dict[str, Any]) -> httpx.Response:
        return await self.client.post(
            self.endpoint_url,
            json=payload,
            headers=headers
        )
        
    async def send_interaction(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sends an interaction payload to the target agent and returns the response.
        """
        if await self.circuit_breaker.is_open():
            raise TargetAgentCircuitBreakerOpen("Target Agent circuit is open due to consecutive failures.")
            
        headers = {"Content-Type": "application/json"}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
            
        try:
            response = await self._send_with_retry(headers, payload)
            response.raise_for_status()
            await self.circuit_breaker.record_success()
            return response.json()
            
        except httpx.HTTPStatusError as e:
            # Only 5xx errors should trip the circuit breaker. 
            # 4xx errors mean our simulator sent bad data, not that the agent is down.
            if e.response.status_code >= 500:
                await self.circuit_breaker.record_failure()
            logger.error(f"Target Agent returned HTTP error: {e}")
            raise
        except httpx.RequestError as e:
            # Reached after retries are exhausted
            await self.circuit_breaker.record_failure()
            logger.error(f"Target Agent network error: {e}")
            raise
