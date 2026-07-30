import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict
from uuid import UUID

from celery import shared_task
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

# Load-bearing despite looking unused: importing celery_app instantiates the
# Celery application, which is what @shared_task binds to.
from backend.worker.celery_app import celery_app  # noqa: F401
from backend.worker.utils import run_async_graph
from backend.db.session import AsyncSessionLocal
from backend.db.models import EvaluationRun, Scenario, TraceMetadata, Judgment
from backend.db.models.scenario import ScenarioSeverity
from backend.db.models.trace_metadata import TraceStatus
from backend.agents.orchestrator import build_evaluation_graph
from backend.core.events import EventBus, RedisEventPublisher
from backend.core.security import extract_bearer_token
from backend.schemas.events import EvaluationEventV1
from backend.core.telemetry import current_run_id
from backend.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

CANCEL_POLL_INTERVAL_SECONDS = 5.0

# Mirrors the reducers declared on EvaluationState. LangGraph applies these
# internally, but a client consuming stream_mode="updates" has to apply them
# itself — plain dict.update() overwrites instead of accumulating, which made
# the reported cost and turn count equal to the *last* node's delta only.
_ADDITIVE_NUMERIC = ("total_cost_usd", "turn_count")
_ADDITIVE_LISTS = ("scenarios", "traces", "judgments", "evolution_suggestions", "errors")


def _merge_state_update(accumulated: Dict[str, Any], delta: Dict[str, Any]) -> None:
    """Apply one node's delta to the accumulated state, honouring the reducers."""
    for key, value in delta.items():
        if key in _ADDITIVE_NUMERIC and isinstance(value, (int, float)):
            accumulated[key] = (accumulated.get(key) or 0) + value
        elif key in _ADDITIVE_LISTS and isinstance(value, list):
            accumulated[key] = list(accumulated.get(key) or []) + value
        else:
            accumulated[key] = value


def _tally(judgments: list[dict]) -> tuple[int, int]:
    passed = sum(1 for j in judgments if j.get("passed"))
    return passed, len(judgments) - passed


# Substrings that identify an upstream quota/rate problem, which is by far the
# most common reason a run produces nothing. Worth naming explicitly because the
# remedy is entirely different from a code fault.
_RATE_LIMIT_MARKERS = ("rate limit", "ratelimit", "429", "quota", "insufficient_quota")


def _summarise_pipeline_errors(errors: list[str]) -> str:
    """
    Turn node-level error strings into one short, actionable message.

    Deliberately does not embed the raw provider payload: those contain nested
    JSON and account identifiers, and this value is returned to API clients.
    """
    joined = " ".join(errors).lower()
    if any(marker in joined for marker in _RATE_LIMIT_MARKERS):
        return (
            "LLM provider rate limit or quota reached, so no scenarios could be "
            "evaluated. Check your provider's usage limits and retry later."
        )
    first = errors[0] if errors else "unknown error"
    # Strip any JSON blob the provider may have appended.
    first = first.split("{")[0].strip() or first
    return f"Evaluation pipeline produced no results: {first[:200]}"


def _pipeline_failed(final_state: Dict[str, Any]) -> str | None:
    """
    Decide whether a graph run that raised no exception actually succeeded.

    Nodes accumulate problems into state["errors"] instead of raising, so the
    graph can complete "successfully" having produced nothing at all. That is how
    a run whose generator hit a provider rate limit was recorded as COMPLETED with
    0 scenarios and no error message.

    A run that produced no scenarios AND no judgments is a failure. Partial
    results stay 'completed' — the per-scenario errors are still recorded.
    """
    errors = final_state.get("errors") or []
    produced_nothing = not final_state.get("scenarios") and not final_state.get("judgments")
    if produced_nothing:
        return _summarise_pipeline_errors(errors) if errors else (
            "Evaluation pipeline produced no scenarios or judgments."
        )
    return None


def _build_graph_config(run: EvaluationRun) -> Dict[str, Any]:
    """
    Translate the persisted run_config into the graph's config dict.

    Every RunConfig field the user can set is forwarded here. Previously only
    five keys were passed, so scenario_count, categories, severity_levels and
    judge_model were validated, stored, and then silently ignored — every run
    generated the generator's fallback of 3 scenarios regardless of the request.
    """
    run_config = run.run_config or {}
    agent = run.agent_config

    return {
        "max_budget_usd": run_config.get("eval_budget_usd", 5.0),
        "scenario_count": run_config.get("scenario_count"),
        "categories": run_config.get("categories") or [],
        "severity_levels": run_config.get("severity_levels") or [],
        "red_team_enabled": run_config.get("red_team_enabled", True),
        "judge_model": run_config.get("judge_model") or settings.DEFAULT_JUDGE_MODEL,
        "pass_threshold": run_config.get("pass_threshold", 70.0),
        "target_endpoint_url": agent.endpoint_url,
        "connector_type": agent.connector_type.value,
        "target_description": agent.description or "An AI agent",
        "target_system_prompt": agent.system_prompt or "You are a helpful assistant",
        "target_tools": agent.tool_definitions or [],
        # Decrypted at dispatch time so the connector can authenticate. The
        # plaintext lives only in worker memory for the duration of the run.
        "target_bearer_token": extract_bearer_token(agent.auth_config_encrypted),
    }


async def _execute_evaluation_run_async(run_id: str):
    logger.info(f"Starting evaluation run {run_id}")

    run_uuid = UUID(run_id)

    async with AsyncSessionLocal() as session:
        stmt = (
            select(EvaluationRun)
            .options(selectinload(EvaluationRun.agent_config))
            .where(EvaluationRun.id == run_uuid)
        )
        run: EvaluationRun | None = (await session.execute(stmt)).scalar_one_or_none()

        if not run:
            logger.error(f"EvaluationRun {run_id} not found.")
            return

        # Atomic Ownership Acquisition: `pending` -> `running`.
        # started_at is set here. It was previously never written anywhere, which
        # left the zombie sweeper's "no heartbeat yet" branch permanently unable
        # to match, so a worker that died in its first 30 seconds stayed
        # 'running' forever.
        from celery import current_task

        task_id = current_task.request.id if current_task else None

        claim = (
            update(EvaluationRun)
            .where(EvaluationRun.id == run_uuid, EvaluationRun.status == "pending")
            .values(
                status="running",
                started_at=datetime.now(timezone.utc),
                last_heartbeat_at=datetime.now(timezone.utc),
                celery_task_id=task_id,
            )
        )
        claim_result = await session.execute(claim)
        if claim_result.rowcount == 0:
            logger.warning(
                f"Run {run_id} is no longer pending or already claimed. Aborting execution."
            )
            return

        await session.commit()

        # Reload to get the claimed row plus its agent config.
        run = (await session.execute(stmt)).scalar_one_or_none()
        if not run:
            logger.error(f"EvaluationRun {run_id} vanished after claim.")
            return

        initial_state = {
            "run_id": run_id,
            "tenant_id": str(run.tenant_id),
            "agent_id": str(run.agent_config.id),
            "config": _build_graph_config(run),
            "total_cost_usd": 0.0,
            "turn_count": 0,
            "scenarios": [],
            "traces": [],
            "judgments": [],
            "evolution_suggestions": [],
            "drift_profile": None,
            "errors": [],
        }
        judge_model = initial_state["config"]["judge_model"]

    # Everything below uses plain locals for terminal-state decisions rather than
    # stashing flags on a (possibly detached or None) ORM instance.
    event_bus: EventBus | None = None
    heartbeat_task: asyncio.Task | None = None
    seq_num = 1
    fatal_error: str | None = None
    cancelled = False
    final_state: Dict[str, Any] = dict(initial_state)

    try:
        event_bus = EventBus(RedisEventPublisher())
        heartbeat_task = asyncio.create_task(_heartbeat_loop(run_id))
        current_run_id.set(run_id)

        logger.info(f"Invoking orchestrator graph for {run_id}")
        graph = build_evaluation_graph()

        await event_bus.publish_evaluation_event(
            EvaluationEventV1(
                seq_num=seq_num, run_id=run_id, status="running", current_node="start"
            )
        )
        seq_num += 1

        current_status = "running"
        last_cancel_check = time.monotonic()

        async for event in graph.astream(dict(initial_state), stream_mode="updates"):
            if time.monotonic() - last_cancel_check > CANCEL_POLL_INTERVAL_SECONDS:
                async with AsyncSessionLocal() as check_session:
                    current_status = await check_session.scalar(
                        select(EvaluationRun.status).where(EvaluationRun.id == run_uuid)
                    )
                last_cancel_check = time.monotonic()

            if current_status == "cancelled":
                logger.info(f"Run {run_id} cancelled by user during execution.")
                final_state.setdefault("errors", []).append("Run cancelled by user.")
                cancelled = True
                break

            for node_name, state_update in event.items():
                _merge_state_update(final_state, state_update)

                passed, failed = _tally(final_state.get("judgments", []))
                await event_bus.publish_evaluation_event(
                    EvaluationEventV1(
                        seq_num=seq_num,
                        run_id=run_id,
                        status="running",
                        current_node=node_name,
                        turn_count=final_state.get("turn_count", 0),
                        total_cost_usd=final_state.get("total_cost_usd", 0.0),
                        passed_scenarios=passed,
                        failed_scenarios=failed,
                    )
                )
                seq_num += 1

        # Persist the output centrally
        async with AsyncSessionLocal() as save_session:
            saved_run = (
                await save_session.execute(stmt)
            ).scalar_one_or_none()
            if saved_run:
                await _persist_final_state(
                    save_session, saved_run, final_state, judge_model
                )
                await save_session.commit()

        # A graph that raised nothing can still have produced nothing.
        if not cancelled:
            fatal_error = _pipeline_failed(final_state)
            if fatal_error:
                logger.error(
                    "Run %s produced no results. errors=%s",
                    run_id, final_state.get("errors"),
                )

        passed, failed = _tally(final_state.get("judgments", []))
        if cancelled:
            terminal_event_status = "cancelled"
        elif fatal_error:
            terminal_event_status = "failed"
        else:
            terminal_event_status = "completed"

        await event_bus.publish_evaluation_event(
            EvaluationEventV1(
                seq_num=seq_num,
                run_id=run_id,
                # "cancelled" is now part of the event contract. Publishing it
                # used to raise ValidationError inside this try block, which the
                # handler below then reported as a failure.
                status=terminal_event_status,
                current_node="end",
                turn_count=final_state.get("turn_count", 0),
                total_cost_usd=final_state.get("total_cost_usd", 0.0),
                passed_scenarios=passed,
                failed_scenarios=failed,
                error_message=fatal_error,
            )
        )

    except Exception as e:
        logger.error(f"Run {run_id} failed: {e}", exc_info=True)
        # Store a generic message on the run; the detail stays in worker logs so
        # internal identifiers (DSNs, SQL, provider payloads) are not exposed
        # through the API's error_message field.
        fatal_error = f"{type(e).__name__}: evaluation run failed. See server logs."

        if event_bus is not None:
            try:
                await event_bus.publish_evaluation_event(
                    EvaluationEventV1(
                        seq_num=seq_num,
                        run_id=run_id,
                        status="failed",
                        current_node="error",
                        error_message=fatal_error,
                    )
                )
            except Exception as publish_error:
                logger.error(f"Could not publish failure event: {publish_error}")

    finally:
        if event_bus is not None:
            await event_bus.close()

        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

        if cancelled:
            terminal_status = "cancelled"
        elif fatal_error:
            terminal_status = "failed"
        else:
            terminal_status = "completed"

        # Stale Writer Rejection: only transition if we still own the run.
        async with AsyncSessionLocal() as terminal_session:
            terminal_update = (
                update(EvaluationRun)
                .where(
                    EvaluationRun.id == run_uuid,
                    EvaluationRun.status.in_(["running", "cancelled"]),
                )
                .values(
                    status=terminal_status,
                    error_message=fatal_error,
                    # completed_at was previously never written, so the API always
                    # reported null for it.
                    completed_at=datetime.now(timezone.utc),
                )
            )
            result = await terminal_session.execute(terminal_update)
            if result.rowcount == 0:
                logger.error(
                    f"Failed to commit terminal state for Run {run_id}. "
                    f"Worker was likely marked stale by the sweeper."
                )
            else:
                await terminal_session.commit()
                logger.info(f"Run {run_id} finished execution ({terminal_status}).")


async def _heartbeat_loop(run_id: str):
    """Periodically updates the last_heartbeat_at timestamp in the database."""
    interval = settings.HEARTBEAT_INTERVAL_SECONDS
    try:
        while True:
            await asyncio.sleep(interval)
            try:
                async with AsyncSessionLocal() as session:
                    result = await session.execute(
                        update(EvaluationRun)
                        .where(
                            EvaluationRun.id == UUID(run_id),
                            EvaluationRun.status == "running",
                        )
                        .values(last_heartbeat_at=datetime.now(timezone.utc))
                    )
                    if result.rowcount:
                        await session.commit()
            except Exception as e:
                logger.warning(f"Heartbeat DB error for run {run_id}: {e}")
    except asyncio.CancelledError:
        pass


def _severity_from(value: Any) -> ScenarioSeverity:
    try:
        return ScenarioSeverity(str(value).lower())
    except ValueError:
        return ScenarioSeverity.MEDIUM


def _trace_status_from(value: Any) -> TraceStatus:
    try:
        return TraceStatus(str(value).lower())
    except ValueError:
        return TraceStatus.COMPLETED


async def _persist_final_state(
    session: AsyncSession,
    run: EvaluationRun,
    final_state: dict,
    judge_model: str,
):
    now = datetime.now(timezone.utc)

    # 1. Scenarios
    scenario_models = {}
    for s in final_state.get("scenarios", []):
        scenario = Scenario(
            evaluation_run_id=run.id,
            title=s.get("title", "Unknown"),
            description=s.get("description", ""),
            # The generator emits `expected_behavior`; it previously emitted only
            # `expected_outcome`, so this column was always empty.
            expected_behavior=s.get("expected_behavior") or s.get("expected_outcome") or "",
            category=s.get("category") or "general",
            severity=_severity_from(s.get("severity")),
            input_payload=s.get("input_payload") or {},
            tags=s.get("tags") or [],
        )
        session.add(scenario)
        scenario_models[s.get("id")] = scenario

    await session.flush()  # Flush to get scenario IDs

    # 2. Traces
    trace_models_by_key = {}
    for t in final_state.get("traces", []):
        scenario = scenario_models.get(t.get("scenario_id"))
        storage_key = t.get("storage_key")

        # Skip traces that failed to upload (no storage_key)
        if not storage_key:
            continue

        trace = TraceMetadata(
            id=UUID(t["trace_id"]),
            evaluation_run_id=run.id,
            scenario_id=scenario.id if scenario else None,
            storage_key=storage_key,
            # Real measurements from the simulator. These were hardcoded to 0,
            # which made every metric column and the status index useless.
            turn_count=t.get("turn_count"),
            token_count=t.get("token_count"),
            duration_ms=t.get("duration_ms"),
            tool_call_count=t.get("tool_call_count"),
            status=_trace_status_from(t.get("status")),
            error_message=t.get("error_message"),
            completed_at=now,
        )
        session.add(trace)
        trace_models_by_key[storage_key] = trace

    await session.flush()

    # 3. Judgments
    passed_count = 0
    failed_count = 0
    scores: list[float] = []

    for j in final_state.get("judgments", []):
        trace = trace_models_by_key.get(j.get("trace_key"))
        if not trace:
            continue

        score = j.get("overall_score")
        if score is None:
            score = j.get("score", 0.0)

        judgment = Judgment(
            evaluation_run_id=run.id,
            scenario_id=trace.scenario_id,
            trace_id=trace.id,
            passed=bool(j.get("passed", False)),
            overall_score=score,
            safety_score=j.get("safety_score"),
            correctness_score=j.get("correctness_score"),
            instruction_following_score=j.get("instruction_following_score"),
            reasoning=j.get("reasoning"),
            raw_output=j.get("raw_output") or {},
            # The model that actually judged, not a hardcoded literal.
            judge_model=j.get("judge_model") or judge_model,
        )
        session.add(judgment)
        scores.append(float(score))

        if judgment.passed:
            passed_count += 1
        else:
            failed_count += 1

    # Atomic increment of counters (safe under retries)
    total = passed_count + failed_count
    run.passed_scenarios = EvaluationRun.passed_scenarios + passed_count
    run.failed_scenarios = EvaluationRun.failed_scenarios + failed_count
    run.total_scenarios = EvaluationRun.total_scenarios + total

    if scores:
        run.avg_score = sum(scores) / len(scores)

    run.drift_profile = final_state.get("drift_profile")
    run.evolution_suggestions = final_state.get("evolution_suggestions")
    run.embedding_model_version = settings.DEFAULT_EMBEDDING_MODEL


@shared_task(
    bind=True,
    name="backend.worker.tasks.execute_evaluation_run",
    soft_time_limit=settings.TASK_SOFT_TIME_LIMIT_SECONDS,
    time_limit=settings.TASK_TIME_LIMIT_SECONDS,
)
def execute_evaluation_run(self, run_id: str):
    """
    Celery task that acts as the entrypoint for an evaluation run.

    Hard/soft time limits mean a wedged run is killed by Celery instead of
    depending entirely on the zombie sweeper.
    """
    run_async_graph(_execute_evaluation_run_async(run_id))


@shared_task(bind=True, name="backend.worker.tasks.delete_s3_traces")
def delete_s3_traces(self, run_id: str, tenant_id: str):
    """
    Celery task to physically remove all traces associated with a run from S3.
    """
    from backend.connectors.s3 import S3BlobStore

    async def _delete():
        s3 = S3BlobStore()
        prefix = f"tenants/{tenant_id}/runs/{run_id}/"
        try:
            await s3.delete_prefix(prefix)
            logger.info(f"Successfully deleted all traces in S3 under {prefix}")
        except Exception as e:
            logger.error(f"Failed to delete S3 traces for {run_id}: {e}")
            raise
        finally:
            await s3.close()

    run_async_graph(_delete())
