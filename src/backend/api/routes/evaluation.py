import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import List
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse

from backend.api.deps import CurrentAdmin, CurrentUser
from backend.core.config import get_settings
from backend.core.telemetry import get_sse_active_connections
from backend.db.models import EvaluationRun, Judgment, Scenario, TraceMetadata
from backend.db.session import get_db
from backend.schemas.evaluation import (
    EvaluationRunCreate,
    EvaluationRunResponse,
    JudgmentResponseModel,
    ScenarioResponse,
    ScenarioResultResponse,
    TraceDownloadResponse,
    TraceResponse,
)
from backend.worker.queue import (
    enqueue_evaluation_run,
    enqueue_s3_trace_cleanup,
    revoke_task,
)

settings = get_settings()

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/evaluations", tags=["evaluations"])

MAX_ACTIVE_RUNS_PER_TENANT = 2
STREAM_TICKET_TTL_SECONDS = 30
TRACE_URL_TTL_SECONDS = 900


async def _get_run_or_404(
    db: AsyncSession, run_id: UUID, tenant_id: UUID
) -> EvaluationRun:
    """Load a run scoped to the caller's tenant, or raise 404."""
    result = await db.execute(
        select(EvaluationRun).where(
            EvaluationRun.id == run_id,
            EvaluationRun.tenant_id == tenant_id,
        )
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Evaluation run not found")
    return run


@router.post("/", response_model=EvaluationRunResponse, status_code=status.HTTP_201_CREATED)
async def create_evaluation_run(
    run_in: EvaluationRunCreate,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger a new evaluation run. This saves the config and queues a Celery task.
    """
    from backend.db.models import AgentConfig

    stmt = select(AgentConfig).where(
        AgentConfig.id == run_in.agent_config_id,
        AgentConfig.tenant_id == current_user.tenant_id,
        AgentConfig.deleted_at.is_(None),
    )
    agent = (await db.execute(stmt)).scalar_one_or_none()

    if not agent:
        raise HTTPException(status_code=404, detail="Agent config not found")

    tenant_id = current_user.tenant_id

    # Enforce inter-run concurrency limit using a transaction-level advisory lock
    # to prevent TOCTOU races between concurrent requests.
    lock_id = hash(tenant_id.int) % (2**63 - 1)
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)").bindparams(lock_id=lock_id)
    )

    active_count = await db.scalar(
        select(func.count(EvaluationRun.id)).where(
            EvaluationRun.tenant_id == tenant_id,
            EvaluationRun.status.in_(["pending", "running"]),
        )
    )
    if active_count >= MAX_ACTIVE_RUNS_PER_TENANT:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Concurrency limit exceeded: You already have "
                f"{MAX_ACTIVE_RUNS_PER_TENANT} active evaluation runs. "
                f"Please wait for them to finish."
            ),
        )

    db_run = EvaluationRun(
        tenant_id=tenant_id,
        agent_config_id=agent.id,
        name=run_in.name,
        run_config=run_in.run_config.model_dump(),
        status="pending",
    )
    db.add(db_run)
    await db.commit()
    await db.refresh(db_run)

    try:
        db_run.celery_task_id = enqueue_evaluation_run(db_run.id)
        await db.commit()
        await db.refresh(db_run)
    except Exception as e:
        logger.error(f"Failed to enqueue Celery task: {e}")
        db_run.status = "failed"
        db_run.error_message = "Failed to enqueue evaluation task."
        db_run.completed_at = datetime.now(timezone.utc)
        await db.commit()
        raise HTTPException(status_code=500, detail="Failed to enqueue evaluation task")

    return db_run


@router.get("/", response_model=List[EvaluationRunResponse])
async def list_evaluation_runs(
    current_user: CurrentUser,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    """
    List evaluation runs for the current tenant.
    """
    stmt = (
        select(EvaluationRun)
        .where(EvaluationRun.tenant_id == current_user.tenant_id)
        .order_by(EvaluationRun.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/{run_id}", response_model=EvaluationRunResponse)
async def get_evaluation_run(
    run_id: UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """
    Get the status and summary stats of an evaluation run.
    """
    return await _get_run_or_404(db, run_id, current_user.tenant_id)


# ── Results ──────────────────────────────────────────────────────────────────
# The judge's per-scenario score and reasoning are the product's actual output.
# They were persisted to Postgres but no endpoint exposed them, so no client
# could ever read them.

@router.get("/{run_id}/results", response_model=List[ScenarioResultResponse])
async def get_evaluation_results(
    run_id: UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """
    Full per-scenario results: each scenario with its trace and the judge's verdict.
    """
    await _get_run_or_404(db, run_id, current_user.tenant_id)

    scenarios = (
        await db.execute(
            select(Scenario)
            .where(Scenario.evaluation_run_id == run_id)
            .options(selectinload(Scenario.traces), selectinload(Scenario.judgments))
            .order_by(Scenario.created_at.asc())
        )
    ).scalars().all()

    results: list[ScenarioResultResponse] = []
    for scenario in scenarios:
        trace = scenario.traces[0] if scenario.traces else None
        judgment = scenario.judgments[0] if scenario.judgments else None
        results.append(
            ScenarioResultResponse(
                scenario=ScenarioResponse.model_validate(scenario),
                trace=TraceResponse.model_validate(trace) if trace else None,
                judgment=(
                    JudgmentResponseModel.model_validate(judgment) if judgment else None
                ),
            )
        )
    return results


@router.get("/{run_id}/scenarios", response_model=List[ScenarioResponse])
async def list_run_scenarios(
    run_id: UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """List the scenarios generated for a run."""
    await _get_run_or_404(db, run_id, current_user.tenant_id)
    result = await db.execute(
        select(Scenario)
        .where(Scenario.evaluation_run_id == run_id)
        .order_by(Scenario.created_at.asc())
    )
    return result.scalars().all()


@router.get("/{run_id}/judgments", response_model=List[JudgmentResponseModel])
async def list_run_judgments(
    run_id: UUID,
    current_user: CurrentUser,
    passed: bool | None = Query(None, description="Filter by pass/fail."),
    db: AsyncSession = Depends(get_db),
):
    """List the judge's verdicts for a run, optionally filtered by outcome."""
    await _get_run_or_404(db, run_id, current_user.tenant_id)
    stmt = select(Judgment).where(Judgment.evaluation_run_id == run_id)
    if passed is not None:
        stmt = stmt.where(Judgment.passed.is_(passed))
    result = await db.execute(stmt.order_by(Judgment.overall_score.asc()))
    return result.scalars().all()


@router.get("/{run_id}/traces", response_model=List[TraceResponse])
async def list_run_traces(
    run_id: UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """List trace metadata for a run."""
    await _get_run_or_404(db, run_id, current_user.tenant_id)
    result = await db.execute(
        select(TraceMetadata)
        .where(TraceMetadata.evaluation_run_id == run_id)
        .order_by(TraceMetadata.created_at.asc())
    )
    return result.scalars().all()


@router.get(
    "/{run_id}/traces/{trace_id}/download", response_model=TraceDownloadResponse
)
async def download_run_trace(
    run_id: UUID,
    trace_id: UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """
    Issue a short-lived presigned URL for the raw trace blob.

    Authorization is enforced here (tenant + run ownership) before the URL is
    minted, as S3BlobStore.generate_presigned_url documents.
    """
    await _get_run_or_404(db, run_id, current_user.tenant_id)

    trace = (
        await db.execute(
            select(TraceMetadata).where(
                TraceMetadata.id == trace_id,
                TraceMetadata.evaluation_run_id == run_id,
            )
        )
    ).scalar_one_or_none()

    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")
    if not trace.storage_key:
        raise HTTPException(
            status_code=409, detail="This trace has no stored payload (simulation failed)."
        )

    # Imported lazily so the API process does not build an S3 client at startup.
    from backend.connectors.s3 import S3BlobStore

    store = S3BlobStore()
    try:
        url = await store.generate_presigned_url(
            trace.storage_key, expires_in_seconds=TRACE_URL_TTL_SECONDS
        )
    except Exception as e:
        logger.error(f"Failed to presign trace {trace_id}: {e}")
        raise HTTPException(status_code=502, detail="Could not generate download URL")
    finally:
        await store.close()

    return TraceDownloadResponse(
        trace_id=trace.id,
        storage_key=trace.storage_key,
        download_url=url,
        expires_in_seconds=TRACE_URL_TTL_SECONDS,
    )


# ── Lifecycle ────────────────────────────────────────────────────────────────

@router.post("/{run_id}/cancel", response_model=EvaluationRunResponse)
async def cancel_evaluation_run(
    run_id: UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """
    Cancel a pending or running evaluation run.
    """
    run = await _get_run_or_404(db, run_id, current_user.tenant_id)

    if run.status in ["completed", "failed", "cancelled"]:
        raise HTTPException(
            status_code=400,
            detail="Cannot cancel a run that has already reached a terminal state.",
        )

    task_id = run.celery_task_id
    run.status = "cancelled"
    await db.commit()
    await db.refresh(run)

    # Actually revoke the task. celery_task_id exists for this purpose but was
    # never used, so cancellation relied solely on the worker's 5s DB poll.
    revoke_task(task_id)

    return run


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_evaluation_run(
    run_id: UUID,
    current_user: CurrentAdmin,
    db: AsyncSession = Depends(get_db),
):
    """
    Delete an evaluation run and its stored traces.

    Order matters: the database row is removed first, then blob cleanup is
    queued. The previous order queued the S3 deletion *before* committing, so a
    failed commit destroyed the traces of a run that still existed. Orphaned
    blobs are recoverable (the periodic cleanup_orphaned_traces job sweeps them);
    traces missing from a live run are not.
    """
    run = await _get_run_or_404(db, run_id, current_user.tenant_id)
    tenant_id = run.tenant_id

    await db.delete(run)
    await db.commit()

    try:
        enqueue_s3_trace_cleanup(run_id, tenant_id)
    except Exception as e:
        # The run is already gone; the periodic sweeper will reclaim the blobs.
        logger.error(
            f"Run {run_id} deleted but S3 cleanup could not be queued: {e}. "
            f"Orphaned blobs will be reclaimed by cleanup_orphaned_traces."
        )

    return None


# ── Streaming ────────────────────────────────────────────────────────────────

@router.post("/{run_id}/stream-ticket")
async def get_stream_ticket(
    run_id: UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """
    Generate a short-lived ticket for authenticating SSE stream connections.
    """
    await _get_run_or_404(db, run_id, current_user.tenant_id)

    ticket = str(uuid.uuid4())
    redis = aioredis.from_url(settings.REDIS_PUBSUB_URL)
    try:
        await redis.setex(f"ticket:{ticket}", STREAM_TICKET_TTL_SECONDS, str(run_id))
    finally:
        await redis.aclose()

    return {"ticket": ticket, "expires_in_seconds": STREAM_TICKET_TTL_SECONDS}


@router.get("/{run_id}/stream")
async def stream_evaluation_run(
    run_id: UUID,
    request: Request,
    ticket: str = Query(...),
    last_seq: int = Query(default=0, ge=0),
):
    """
    Stream evaluation run progress via Server-Sent Events (SSE).
    Authenticates via short-lived ticket to prevent token leakage.

    On reconnection, pass `last_seq` (the last seq_num you received) to replay
    any events missed during the disconnection window.
    """
    redis = aioredis.from_url(settings.REDIS_PUBSUB_URL)

    try:
        ticket_key = f"ticket:{ticket}"
        authorized_run_id = await redis.get(ticket_key)
        if not authorized_run_id or authorized_run_id.decode("utf-8") != str(run_id):
            raise HTTPException(status_code=401, detail="Invalid or expired stream ticket")

        # Consume the ticket so it can't be reused
        await redis.delete(ticket_key)

        async def event_generator(redis_client):
            sse_gauge = get_sse_active_connections()
            sse_gauge.add(1)
            pubsub = None
            channel_name = f"eval_stream:{run_id}"
            try:
                if last_seq > 0:
                    from backend.core.events import RedisEventPublisher

                    replay_publisher = RedisEventPublisher()
                    try:
                        missed_events = await replay_publisher.get_replay_events(
                            str(run_id), last_seq
                        )
                        for event_data in missed_events:
                            yield {"data": event_data}
                    finally:
                        await replay_publisher.close()

                pubsub = redis_client.pubsub()
                await pubsub.subscribe(channel_name)
                logger.info(f"Subscribed to {channel_name}")

                while True:
                    if await request.is_disconnected():
                        break

                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=1.0
                    )
                    if message and message["type"] == "message":
                        data = message["data"]
                        if isinstance(data, bytes):
                            data = data.decode("utf-8")
                        yield {"data": data}

                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                pass
            finally:
                # Always tear down, even if the replay step raised, so a failed
                # replay cannot leak a pubsub subscription or skew the gauge.
                if pubsub is not None:
                    try:
                        await pubsub.unsubscribe(channel_name)
                    except Exception:
                        pass
                await redis_client.aclose()
                sse_gauge.add(-1)
                logger.info(f"Unsubscribed from {channel_name}")

        return EventSourceResponse(event_generator(redis))
    except Exception:
        await redis.aclose()
        raise


@router.post("/{run_id}/baseline", response_model=EvaluationRunResponse)
async def set_golden_baseline(
    run_id: UUID,
    current_user: CurrentAdmin,
    db: AsyncSession = Depends(get_db),
):
    """
    Set an evaluation run as the Golden Baseline. This clears the baseline flag
    from all other runs belonging to the same agent configuration, ensuring only
    one baseline exists at a time (Atomic operation).
    """
    run = await _get_run_or_404(db, run_id, current_user.tenant_id)

    if run.status != "completed":
        raise HTTPException(
            status_code=400, detail="Only completed runs can be set as a baseline"
        )

    if not run.avg_score or run.avg_score < 70:
        raise HTTPException(
            status_code=400,
            detail="Run must have a decent average score (>=70) to serve as a baseline",
        )

    agent_id = run.agent_config_id

    unset_stmt = (
        update(EvaluationRun)
        .where(
            EvaluationRun.agent_config_id == agent_id,
            EvaluationRun.tenant_id == current_user.tenant_id,
            EvaluationRun.is_baseline.is_(True),
        )
        .values(is_baseline=False)
    )
    await db.execute(unset_stmt)

    run.is_baseline = True

    await db.commit()
    await db.refresh(run)

    return run
