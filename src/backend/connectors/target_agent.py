import ipaddress
import logging
import socket
import typing
from urllib.parse import urlparse

import httpcore
import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from backend.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class SSRFViolationError(Exception):
    pass


class TargetAgentCircuitBreakerOpen(Exception):
    pass


def _is_forbidden_ip(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """
    Reject any address that could reach internal infrastructure.

    `is_private` alone is not enough: it misses multicast, reserved ranges and
    the unspecified address, and for IPv6 it does not cover mapped IPv4
    loopback (::ffff:127.0.0.1), which is a classic SSRF bypass.
    """
    # Unwrap IPv4-mapped IPv6 (e.g. ::ffff:169.254.169.254) before judging it.
    mapped = getattr(ip_obj, "ipv4_mapped", None)
    if mapped is not None:
        ip_obj = mapped

    return bool(
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local        # includes 169.254.169.254 cloud metadata
        or ip_obj.is_reserved
        or ip_obj.is_multicast
        or ip_obj.is_unspecified
    )


def _resolve_safe_ip(host: str, port: int) -> str:
    """
    Resolve `host` and return an address that passed validation.

    Every resolved address is checked, not just the first one, so a hostname
    that returns a mix of public and private records cannot smuggle a private
    target through. The caller then connects to the returned IP directly, which
    closes the DNS-rebinding (TOCTOU) window between check and connect.
    """
    try:
        addr_info = socket.getaddrinfo(
            host, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM
        )
    except socket.gaierror as exc:
        raise SSRFViolationError(f"Could not resolve hostname: {host}") from exc

    if not addr_info:
        raise SSRFViolationError(f"Could not resolve hostname: {host}")

    for info in addr_info:
        candidate = info[4][0]
        try:
            ip_obj = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if _is_forbidden_ip(ip_obj):
            raise SSRFViolationError(
                f"Hostname {host} resolved to restricted address {candidate}."
            )

    # All records validated; connect to the first one.
    return addr_info[0][4][0]


class SSRFSafeBackend(httpcore.AnyIOBackend):
    """
    Network backend that enforces SSRF protection at connection time.

    It resolves the hostname, validates every resolved address, then connects
    directly to a validated IP. Connecting by IP is what prevents a DNS
    rebinding attack from swapping in a private address after the check.

    TLS is unaffected: httpcore calls `start_tls(server_hostname=...)` with the
    original origin host, not the address passed to connect_tcp, so certificate
    and SNI verification still happen against the real hostname.
    """

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: typing.Iterable[typing.Any] | None = None,
    ):
        safe_ip = _resolve_safe_ip(host, port)
        return await super().connect_tcp(
            safe_ip,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


class CircuitBreaker:
    """Redis-backed Circuit Breaker to share state across distributed workers."""

    def __init__(self, key_prefix: str, failure_threshold: int = 4):
        self.failure_threshold = failure_threshold
        import redis.asyncio as redis

        self.redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
        self.key = f"circuit_breaker:{key_prefix}"

    async def record_failure(self):
        try:
            failures = await self.redis.incr(self.key)
            if failures == 1:
                # Expire failures after 60 seconds of no new failures
                await self.redis.expire(self.key, 60)
        except Exception as e:
            logger.warning(f"Redis circuit breaker record_failure error: {e}. Failing open.")

    async def record_success(self):
        try:
            await self.redis.delete(self.key)
        except Exception as e:
            logger.warning(f"Redis circuit breaker record_success error: {e}. Failing open.")

    async def is_open(self) -> bool:
        try:
            failures_str = await self.redis.get(self.key)
            if failures_str:
                return int(failures_str) >= self.failure_threshold
            return False
        except Exception as e:
            logger.warning(f"Redis circuit breaker is_open error: {e}. Failing open.")
            return False

    async def close(self):
        await self.redis.aclose()


class TargetAgentConnector:
    """
    Connects to an external Target Agent REST API to simulate evaluation interactions.
    Includes strict timeouts, SSRF protection (TOCTOU safe), connection pooling,
    and a Redis-backed Circuit Breaker.

    SSRF enforcement is driven by `settings.ssrf_protection_active` rather than a
    raw APP_ENV string comparison, so it is on in production, off for local
    development against localhost targets, and can be forced on for tests via
    SSRF_PROTECTION_ENABLED=true.
    """

    def __init__(self, endpoint_url: str, bearer_token: str | None = None):
        parsed = urlparse(endpoint_url)
        self.ssrf_enforced = settings.ssrf_protection_active

        if self.ssrf_enforced and parsed.scheme != "https":
            raise SSRFViolationError("Only HTTPS URLs are allowed when SSRF protection is active.")
        if parsed.scheme not in ("http", "https"):
            raise SSRFViolationError(f"Unsupported URL scheme: {parsed.scheme or '(none)'}")
        if not parsed.hostname:
            raise SSRFViolationError("Target endpoint URL has no hostname.")

        self.endpoint_url = endpoint_url
        self.bearer_token = bearer_token
        # 5 second connect, 30 second read
        self.timeout = httpx.Timeout(30.0, connect=5.0)

        # Redis-backed Circuit Breaker
        self.circuit_breaker = CircuitBreaker(
            key_prefix=parsed.hostname,
            failure_threshold=4,
        )

        if self.ssrf_enforced:
            transport = httpx.AsyncHTTPTransport()
            transport._pool._network_backend = SSRFSafeBackend()
            self.client = httpx.AsyncClient(timeout=self.timeout, transport=transport)
        else:
            self.client = httpx.AsyncClient(timeout=self.timeout)

    async def close(self):
        await self.client.aclose()
        await self.circuit_breaker.close()

    @retry(
        retry=retry_if_exception_type(httpx.RequestError),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def _send_with_retry(
        self, headers: dict[str, str], payload: dict[str, typing.Any]
    ) -> httpx.Response:
        return await self.client.post(self.endpoint_url, json=payload, headers=headers)

    async def send_interaction(self, payload: dict[str, typing.Any]) -> dict[str, typing.Any]:
        """
        Sends an interaction payload to the target agent and returns the response.
        """
        if await self.circuit_breaker.is_open():
            raise TargetAgentCircuitBreakerOpen(
                "Target Agent circuit is open due to consecutive failures."
            )

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
            # Log only the status and target, never the full exception repr: it
            # embeds the request URL, which may carry credentials in its query.
            logger.error(
                "Target agent returned HTTP %s for %s",
                e.response.status_code,
                urlparse(self.endpoint_url).hostname,
            )
            raise
        except httpx.RequestError as e:
            # Reached after retries are exhausted
            await self.circuit_breaker.record_failure()
            logger.error(
                "Target agent network error (%s) for %s",
                type(e).__name__,
                urlparse(self.endpoint_url).hostname,
            )
            raise
