"""
Tests for the rate limiting middleware.
"""

import pytest
from unittest.mock import patch, AsyncMock

from backend.core.rate_limit import _get_tier, _get_client_ip


class TestGetTier:
    def test_health_is_exempt(self):
        assert _get_tier("/health") is None

    def test_docs_is_exempt(self):
        assert _get_tier("/docs") is None

    def test_login_is_auth_tier(self):
        assert _get_tier("/api/v1/auth/login") == "auth"

    def test_register_is_auth_tier(self):
        assert _get_tier("/api/v1/auth/register") == "auth"

    def test_evaluations_is_default_tier(self):
        assert _get_tier("/api/v1/evaluations/") == "default"

    def test_agent_configs_is_default_tier(self):
        assert _get_tier("/api/v1/agent-configs/") == "default"


class TestGetClientIp:
    def test_direct_connection(self):
        from unittest.mock import MagicMock
        request = MagicMock()
        request.headers = {}
        request.client.host = "192.168.1.100"
        assert _get_client_ip(request) == "192.168.1.100"

    def test_forwarded_header_single(self):
        from unittest.mock import MagicMock
        request = MagicMock()
        request.headers = {"X-Forwarded-For": "203.0.113.50"}
        assert _get_client_ip(request) == "203.0.113.50"

    def test_forwarded_header_chain(self):
        from unittest.mock import MagicMock
        request = MagicMock()
        request.headers = {"X-Forwarded-For": "203.0.113.50, 70.41.3.18, 150.172.238.178"}
        assert _get_client_ip(request) == "203.0.113.50"
