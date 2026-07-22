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

# Max events stored per run for replay (prevents unbounded memory use)
MAX_REPLAY_BUFFER_SIZE = 200
# TTL for the replay buffer (auto-expire after run is likely done)
REPLAY_BUFFER_TTL_SECONDS = 3600  # 1 hour


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

    async def store_for_replay(self, run_id: str, message: str) -> None:
        """Store event in a Redis list for replay on reconnection."""
        key = f"eval_replay:{run_id}"
        try:
            pipe = self.redis.pipeline()
            pipe.rpush(key, message)
            pipe.ltrim(key, -MAX_REPLAY_BUFFER_SIZE, -1)  # Keep only last N
            pipe.expire(key, REPLAY_BUFFER_TTL_SECONDS)
            await pipe.execute()
        except Exception as e:
            # Non-critical — replay is best-effort
            logger.warning(f"Failed to store replay event for {run_id}: {e}")

    async def get_replay_events(self, run_id: str, after_seq: int) -> list[str]:
        """
        Retrieve stored events for a run that have seq_num > after_seq.
        Returns raw JSON strings ready to send to the client.
        """
        key = f"eval_replay:{run_id}"
        try:
            all_events = await self.redis.lrange(key, 0, -1)
            replay = []
            for raw in all_events:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                try:
                    parsed = json.loads(raw)
                    if parsed.get("seq_num", 0) > after_seq:
                        replay.append(raw)
                except (json.JSONDecodeError, KeyError):
                    continue
            return replay
        except Exception as e:
            logger.warning(f"Failed to read replay buffer for {run_id}: {e}")
            return []

    async def close(self):
        await self.redis.close()


class EventBus:
    """
    High-level Event Bus facade.
    Handles schema validation, serialization, OTel metrics, replay storage, and delegation to transport.
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
            
            # Also store for replay on reconnection
            if hasattr(self.publisher, "store_for_replay"):
                await self.publisher.store_for_replay(event.run_id, payload)
            
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
