import pytest
import httpx
from unittest.mock import patch, AsyncMock

from backend.connectors.target_agent import (
    TargetAgentConnector, 
    SSRFViolationError, 
    TargetAgentCircuitBreakerOpen,
    CircuitBreaker,
    SSRFSafeBackend
)


def test_ssrf_protection_development_mode(monkeypatch):
    """In development, localhost is allowed."""
    monkeypatch.setattr("backend.connectors.target_agent.settings.APP_ENV", "development")
    
    # Should not raise
    TargetAgentConnector("http://localhost:8000/agent")
    TargetAgentConnector("http://127.0.0.1:8000/agent")


def test_ssrf_protection_production_mode(monkeypatch):
    """In production, HTTP is blocked at init."""
    monkeypatch.setattr("backend.connectors.target_agent.settings.APP_ENV", "production")
    
    with pytest.raises(SSRFViolationError, match="HTTPS"):
        TargetAgentConnector("http://google.com")


@pytest.mark.asyncio
async def test_circuit_breaker_opens_on_failures(monkeypatch):
    monkeypatch.setattr("backend.connectors.target_agent.settings.APP_ENV", "development")
    
    # Mock redis to avoid requiring actual redis server for unit tests
    mock_redis = AsyncMock()
    mock_redis.incr.return_value = 4  # Simulate reaching threshold
    mock_redis.get.return_value = "4" # Simulate circuit open
    
    with patch("redis.asyncio.from_url", return_value=mock_redis):
        connector = TargetAgentConnector("http://localhost:9999/dummy")
        
        # 5th attempt should raise CircuitBreakerOpen because get() returns "4" >= 4
        with pytest.raises(TargetAgentCircuitBreakerOpen):
            await connector.send_interaction({"test": "data"})
            
        await connector.close()


@pytest.mark.asyncio
async def test_circuit_breaker_records_success(monkeypatch):
    monkeypatch.setattr("backend.connectors.target_agent.settings.APP_ENV", "development")
    
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None # Circuit closed
    
    with patch("redis.asyncio.from_url", return_value=mock_redis):
        connector = TargetAgentConnector("http://localhost:9999/dummy")
        
        class MockResponse:
            def raise_for_status(self): pass
            def json(self): return {"status": "ok"}
            
        with patch.object(connector.client, "post", new_callable=AsyncMock, return_value=MockResponse()):
            res = await connector.send_interaction({"test": "data"})
            assert res == {"status": "ok"}
            
        # Verify success was recorded (redis key deleted)
        mock_redis.delete.assert_called_once_with(connector.circuit_breaker.key)
        await connector.close()

@pytest.mark.asyncio
async def test_ssrf_backend_blocks_private_ip(monkeypatch):
    monkeypatch.setattr("backend.connectors.target_agent.settings.APP_ENV", "production")
    backend = SSRFSafeBackend()
    
    # Mock socket.getaddrinfo to return a private IP
    import socket
    with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('169.254.169.254', 80))]):
        with pytest.raises(SSRFViolationError, match="restricted private range"):
            await backend.connect_tcp("metadata.aws.internal", 80)
