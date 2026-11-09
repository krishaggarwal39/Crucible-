# ADR-001: LangGraph for Agent Evaluation Orchestration

## Status
Accepted

## Context
Crucible needs to orchestrate a multi-step AI evaluation pipeline: generate scenarios, simulate interactions, judge results, analyze drift, and propose evolutions. Each step depends on the previous one's output and the whole pipeline must be:
- Budget-aware (halt if cost exceeds limit)
- Cancellable mid-execution
- Observable (stream progress events)
- Resilient to partial failures

Options considered:
1. **Plain async functions** — chain coroutines manually
2. **Celery chains/chords** — use Celery's built-in DAG primitives
3. **LangGraph** — state machine with typed state and conditional routing

## Decision
Use **LangGraph** (from LangChain ecosystem) as the orchestration layer.

## Rationale

**Why not plain async?**
- No built-in state management across steps
- Budget checking and conditional routing would be ad-hoc if/else blocks
- No streaming support — we'd have to build our own event emission layer
- Testing individual nodes in isolation requires manual fixture setup

**Why not Celery chains?**
- Celery tasks are fire-and-forget; inspecting intermediate state requires polling the result backend
- No native concept of "budget exceeded, skip remaining steps"
- Streaming progress from inside a Celery chain to a WebSocket/SSE client is complex
- State passing between chained tasks requires serialization/deserialization at each hop

**Why LangGraph?**
- `StateGraph` with `TypedDict` gives us typed, additive state (`operator.add` for lists means parallel-safe merges)
- Conditional edges (`route_after_generation`) cleanly express "if budget exceeded → END"
- `graph.astream(state, stream_mode="updates")` yields node completions as they happen — perfect for SSE
- Each node is a pure async function that takes state and returns a state delta — trivially unit-testable
- Cost tracking is a first-class state field that accumulates automatically

## Consequences
- **Positive**: Clean separation of concerns, budget enforcement is declarative, streaming is free
- **Positive**: Adding a new node (e.g., "remediation validator") is a 5-line change
- **Negative**: LangGraph is a relatively young library — API may change
- **Negative**: Debugging graph execution requires understanding LangGraph's internal dispatch
- **Negative**: A client consuming `stream_mode="updates"` receives *deltas* and must apply
  the same reducers itself. Merging them with a plain `dict.update()` silently overwrites
  the additive fields, which is how reported cost and turn counts once ended up equal to
  the last node's delta rather than the run total.
- **Mitigation**: `langgraph>=0.1.7` with a lower bound only (not a hard pin — see below);
  the wrapper in `orchestrator.py` isolates the graph from business logic; and
  `worker/tasks.py::_merge_state_update` applies the declared reducers explicitly, with a
  test asserting its output equals `graph.ainvoke()`'s own accumulation.

## Note on pinning
An earlier revision of this ADR claimed the LangGraph version was pinned. It is not:
`pyproject.toml` specifies `langgraph>=0.1.7`, which is a floor. Reproducible builds come
from `uv.lock` instead. If a hard pin is wanted, change the specifier — do not rely on this
document as evidence that one exists.
