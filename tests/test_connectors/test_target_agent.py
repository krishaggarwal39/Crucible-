import pytest
from httpx import Response
from backend.connectors.target_agent import TargetAgentConnector

@pytest.mark.asyncio
async def test_target_agent_circuit_breaker_5xx(mocker):
    # Mock httpx.AsyncClient.post to raise HTTPStatusError with 500
    mock_post = mocker.patch("httpx.AsyncClient.post")
    from httpx import HTTPStatusError, Request
    mock_post.side_effect = HTTPStatusError("Server error", request=Request("POST", "http://test"), response=Response(500, request=Request("POST", "http://test")))
    
    # Need to mock redis circuit breaker
    mock_redis = mocker.patch("redis.asyncio.from_url")
    mock_redis_instance = mocker.AsyncMock()
    mock_redis.return_value = mock_redis_instance
    mock_redis_instance.incr.return_value = 1
    mock_redis_instance.get.return_value = "0"
    
    connector = TargetAgentConnector("http://test.com")
    
    with pytest.raises(HTTPStatusError):
        await connector.send_interaction({"message": "test"})
        
    mock_redis_instance.incr.assert_called_once()
    await connector.close()

@pytest.mark.asyncio
async def test_target_agent_circuit_breaker_4xx(mocker):
    # Mock httpx.AsyncClient.post to raise HTTPStatusError with 400
    mock_post = mocker.patch("httpx.AsyncClient.post")
    from httpx import HTTPStatusError, Request
    mock_post.side_effect = HTTPStatusError("Bad Request", request=Request("POST", "http://test"), response=Response(400, request=Request("POST", "http://test")))
    
    mock_redis = mocker.patch("redis.asyncio.from_url")
    mock_redis_instance = mocker.AsyncMock()
    mock_redis.return_value = mock_redis_instance
    mock_redis_instance.get.return_value = "0"
    
    connector = TargetAgentConnector("http://test.com")
    
    with pytest.raises(HTTPStatusError):
        await connector.send_interaction({"message": "test"})
        
    # 4xx should NOT trip circuit breaker
    mock_redis_instance.incr.assert_not_called()
    await connector.close()
