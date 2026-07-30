"""
Tests for the rate limiting middleware.
"""

from unittest.mock import MagicMock

from backend.core.rate_limit import _get_client_ip, _get_tier


def _request(headers: dict | None = None, peer: str = "192.168.1.100"):
    request = MagicMock()
    request.headers = headers or {}
    request.client.host = peer
    return request


class TestGetTier:
    def test_health_is_exempt(self):
        assert _get_tier("/health") is None

    def test_docs_is_exempt(self):
        assert _get_tier("/docs") is None

    def test_openapi_schema_is_exempt(self):
        """The schema lives at /api/v1/openapi.json, not /openapi.json."""
        assert _get_tier("/api/v1/openapi.json") is None

    def test_readiness_is_rate_limited(self):
        """/health/ready touches the database, so it must not be exempt."""
        assert _get_tier("/health/ready") == "default"

    def test_login_is_auth_tier(self):
        assert _get_tier("/api/v1/auth/login") == "auth"

    def test_register_is_auth_tier(self):
        assert _get_tier("/api/v1/auth/register") == "auth"

    def test_evaluations_is_default_tier(self):
        assert _get_tier("/api/v1/evaluations/") == "default"

    def test_agent_configs_is_default_tier(self):
        assert _get_tier("/api/v1/agent-configs/") == "default"


class TestGetClientIp:
    """
    X-Forwarded-For must only be trusted when a proxy count is configured.

    Trusting the leftmost XFF entry unconditionally let any caller pick its own
    rate-limit bucket, which fully bypassed the login limiter.
    """

    def test_direct_connection(self, mocker):
        mocker.patch("backend.core.rate_limit.settings.TRUSTED_PROXY_COUNT", 0)
        assert _get_client_ip(_request()) == "192.168.1.100"

    def test_forwarded_header_ignored_when_no_trusted_proxy(self, mocker):
        mocker.patch("backend.core.rate_limit.settings.TRUSTED_PROXY_COUNT", 0)
        request = _request({"X-Forwarded-For": "203.0.113.50"})
        # Must fall back to the real peer, not the caller-supplied header.
        assert _get_client_ip(request) == "192.168.1.100"

    def test_spoofed_header_cannot_change_identity(self, mocker):
        mocker.patch("backend.core.rate_limit.settings.TRUSTED_PROXY_COUNT", 0)
        first = _get_client_ip(_request({"X-Forwarded-For": "1.1.1.1"}))
        second = _get_client_ip(_request({"X-Forwarded-For": "2.2.2.2"}))
        assert first == second == "192.168.1.100"

    def test_single_trusted_proxy_uses_real_peer(self, mocker):
        mocker.patch("backend.core.rate_limit.settings.TRUSTED_PROXY_COUNT", 1)
        request = _request({"X-Forwarded-For": "203.0.113.50"}, peer="10.0.0.1")
        assert _get_client_ip(request) == "203.0.113.50"

    def test_trusted_proxy_takes_rightmost_untrusted_hop(self, mocker):
        """
        With one trusted proxy, the client-controlled prefix must be ignored and
        the hop our proxy observed used instead.
        """
        mocker.patch("backend.core.rate_limit.settings.TRUSTED_PROXY_COUNT", 1)
        request = _request(
            {"X-Forwarded-For": "1.2.3.4, 203.0.113.50, 70.41.3.18"}, peer="10.0.0.1"
        )
        assert _get_client_ip(request) == "70.41.3.18"

    def test_two_trusted_proxies(self, mocker):
        mocker.patch("backend.core.rate_limit.settings.TRUSTED_PROXY_COUNT", 2)
        request = _request(
            {"X-Forwarded-For": "1.2.3.4, 203.0.113.50, 70.41.3.18"}, peer="10.0.0.1"
        )
        assert _get_client_ip(request) == "203.0.113.50"

    def test_trusted_proxy_with_empty_header_falls_back(self, mocker):
        mocker.patch("backend.core.rate_limit.settings.TRUSTED_PROXY_COUNT", 1)
        request = _request({"X-Forwarded-For": ""}, peer="10.0.0.1")
        assert _get_client_ip(request) == "10.0.0.1"
