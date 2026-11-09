# ADR-002: Redis Pub/Sub for Real-Time SSE Streaming

## Status
Accepted

## Context
Evaluation runs execute in Celery workers (separate processes from the FastAPI API). Clients need real-time progress updates. The question is how to bridge events from the worker to the client's HTTP connection.

Options considered:
1. **WebSockets** — bidirectional persistent connection
2. **Polling** — client repeatedly hits GET /evaluations/{id}
3. **Redis Pub/Sub → SSE** — worker publishes to Redis channel, API subscribes and streams via Server-Sent Events
4. **Kafka/RabbitMQ** — message queue with consumer groups

## Decision
Use **Redis Pub/Sub** as the event transport, with **SSE (Server-Sent Events)** as the client protocol.

## Rationale

**Why not WebSockets?**
- WebSockets are bidirectional — we only need server→client (unidirectional)
- WebSocket connections don't survive behind load balancers/CDNs as well as SSE
- SSE auto-reconnects natively in browsers (EventSource API)
- WebSocket auth is complex (can't use standard HTTP headers in the upgrade request)

**Why not polling?**
- 3-5 second polling creates unnecessary DB load at scale
- Poor UX — progress feels "jerky" instead of smooth
- With 100 concurrent runs, that's 20-30 queries/second just for polling

**Why not Kafka?**
- Massive overkill for our throughput (< 100 concurrent runs)
- Adds operational complexity (ZooKeeper, partition management)
- Redis is already in our stack for Celery broker + caching

**Why Redis Pub/Sub + SSE?**
- Redis is already a required dependency (Celery broker)
- Pub/Sub is fire-and-forget, zero persistence overhead
- SSE is HTTP-native — works through nginx, CDNs, and corporate proxies
- `sse-starlette` integrates cleanly with FastAPI's async generators
- Replay buffer (Redis list) handles reconnection gaps without full Kafka semantics

**Security: Ticket-based SSE auth**
- SSE (EventSource) doesn't support custom headers → can't pass Bearer token
- Solution: client gets a 30-second single-use ticket via POST, then opens SSE with `?ticket=`
- Prevents JWT leakage in URLs (tokens would appear in access logs, browser history)

## Consequences
- **Positive**: Sub-second event delivery, zero additional infrastructure
- **Positive**: Native browser reconnection via EventSource
- **Positive**: Replay buffer eliminates "missed events" on brief disconnects
- **Negative**: Redis Pub/Sub is at-most-once delivery — if Redis restarts, in-flight messages are lost
- **Negative**: Pub/Sub doesn't scale horizontally the same way Kafka does (single Redis instance bottleneck)
- **Mitigation**: For our scale (< 1000 concurrent connections), single Redis is sufficient. Replay buffer covers transient drops. Terminal state is always available via REST GET.
