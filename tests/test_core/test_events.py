"""
Tests for the EventBus and Redis publisher.
"""

import pytest
from unittest.mock import patch, AsyncMock

from backend.core.events import EventBus, RedisEventPublisher
from backend.schemas.events import EvaluationEventV1


@pytest.mark.asyncio
async def test_event_bus_publish_success():
    """EventBus should serialize and publish valid events."""
    mock_publisher = AsyncMock()

    bus = EventBus(mock_publisher)
    event = EvaluationEventV1(
        seq_num=1,
        run_id="run-123",
        status="running",
        current_node="generate",
        turn_count=1,
        total_cost_usd=0.01,
    )

    await bus.publish_evaluation_event(event)

    mock_publisher.publish.assert_called_once()
    call_args = mock_publisher.publish.call_args
    assert call_args[0][0] == "eval_stream:run-123"
    # Verify it's valid JSON
    import json
    payload = json.loads(call_args[0][1])
    assert payload["run_id"] == "run-123"
    assert payload["status"] == "running"
    assert payload["seq_num"] == 1


@pytest.mark.asyncio
async def test_event_bus_publish_transport_error():
    """Transport errors should be caught and counted as dropped."""
    mock_publisher = AsyncMock()
    mock_publisher.publish.side_effect = ConnectionError("Redis down")

    bus = EventBus(mock_publisher)
    event = EvaluationEventV1(
        seq_num=1,
        run_id="run-123",
        status="running",
        current_node="generate",
    )

    # Should not raise
    await bus.publish_evaluation_event(event)


@pytest.mark.asyncio
async def test_event_bus_close():
    """Close should propagate to the publisher."""
    mock_publisher = AsyncMock()
    bus = EventBus(mock_publisher)
    await bus.close()
    mock_publisher.close.assert_called_once()


@pytest.mark.asyncio
async def test_redis_event_publisher_publish():
    """RedisEventPublisher should call redis.publish."""
    with patch("redis.asyncio.from_url") as mock_from_url:
        mock_redis = AsyncMock()
        mock_from_url.return_value = mock_redis

        publisher = RedisEventPublisher()
        await publisher.publish("channel:test", '{"data": "hello"}')

        mock_redis.publish.assert_called_once_with("channel:test", '{"data": "hello"}')


@pytest.mark.asyncio
async def test_redis_event_publisher_publish_failure():
    """Redis publish failure should raise (EventBus handles it)."""
    with patch("redis.asyncio.from_url") as mock_from_url:
        mock_redis = AsyncMock()
        mock_redis.publish.side_effect = ConnectionError("Connection lost")
        mock_from_url.return_value = mock_redis

        publisher = RedisEventPublisher()
        with pytest.raises(ConnectionError):
            await publisher.publish("channel:test", "data")


class TestEvaluationEventV1Schema:
    def test_valid_event_creation(self):
        event = EvaluationEventV1(
            seq_num=1,
            run_id="abc",
            status="running",
            current_node="judge",
        )
        assert event.turn_count == 0
        assert event.total_cost_usd == 0.0
        assert event.timestamp is not None

    def test_event_serialization(self):
        event = EvaluationEventV1(
            seq_num=5,
            run_id="run-1",
            status="completed",
            current_node="end",
            passed_scenarios=8,
            failed_scenarios=2,
            total_cost_usd=1.23,
        )
        data = event.model_dump()
        assert data["passed_scenarios"] == 8
        assert data["failed_scenarios"] == 2
