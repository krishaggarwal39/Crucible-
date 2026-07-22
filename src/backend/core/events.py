import json
import logging
import time
from typing import Protocol

import redis.asyncio as aioredis
from pydantic import ValidationError

from backend.schemas.events import BaseEvent, EvaluationEventV1
from backend.core.config import get_settings
from backend.core.telemetry import events_published_total, events_dropped_total, events_publish_duration

logger = logging.getLogger(__name__)
settings = get_settings()

class EventPublisher(Protocol):
    """Abstract protocol for event transport."""
    async def publish(self, channel: str, message: str) -> None:
        ...

class RedisEventPublisher:
    """Redis Pub/Sub implementation of EventPublisher."""
    def __init__(self):
        self.redis = aioredis.from_url(settings.REDIS_PUBSUB_URL)

    async def publish(self, channel: str, message: str) -> None:
        try:
            await self.redis.publish(channel, message)
        except Exception as e:
            logger.error(f"Failed to publish to redis channel {channel}: {e}")
            raise

    async def close(self):
        await self.redis.close()


class EventBus:
    """
    High-level Event Bus facade.
    Handles schema validation, serialization, OTel metrics, and delegation to transport.
    """
    def __init__(self, publisher: EventPublisher):
        self.publisher = publisher

    async def publish_evaluation_event(self, event: EvaluationEventV1) -> None:
        """Publishes an EvaluationEventV1 safely with full observability."""
        start_time = time.perf_counter()
        channel = f"eval_stream:{event.run_id}"
        
        try:
            # Pydantic validation is already enforced by the type hint/instantiation,
            # but we serialize it here.
            payload = event.model_dump_json()
            
            await self.publisher.publish(channel, payload)
            
            # Metrics: Success
            events_published_total.add(1, {"event_type": "EvaluationEventV1", "status": event.status})
            duration = time.perf_counter() - start_time
            events_publish_duration.record(duration)
            
        except ValidationError as ve:
            logger.error(f"Schema validation failed before publish: {ve}")
            events_dropped_total.add(1, {"reason": "validation_error"})
        except Exception as e:
            logger.error(f"EventBus publish failed: {e}")
            events_dropped_total.add(1, {"reason": "transport_error"})
            
    async def close(self):
        if hasattr(self.publisher, "close"):
            await self.publisher.close()
