import logging
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List

from backend.db.session import get_db
from backend.db.models import EvaluationRun, AgentConfig
from backend.schemas.evaluation import EvaluationRunCreate, EvaluationRunResponse
from backend.worker.tasks import execute_evaluation_run
from backend.core.config import get_settings
from backend.core.telemetry import sse_active_connections
import asyncio
from sse_starlette.sse import EventSourceResponse
import redis.asyncio as aioredis
from fastapi import Request
from backend.api.deps import CurrentUser, CurrentAdmin

settings = get_settings()

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/evaluations", tags=["evaluations"])

@router.post("/", response_model=EvaluationRunResponse, status_code=status.HTTP_201_CREATED)
async def create_evaluation_run(
    run_in: EvaluationRunCreate,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db)
):
    """
    Trigger a new evaluation run. This saves the config and queues a Celery task.
    """
    # Verify agent exists and belongs to tenant
    stmt = select(AgentConfig).where(
        AgentConfig.id == run_in.agent_config_id,
        AgentConfig.tenant_id == current_user.tenant_id
    )
    result = await db.execute(stmt)
    agent = result.scalar_one_or_none()
    
    if not agent:
        raise HTTPException(status_code=404, detail="Agent config not found")
        
    from sqlalchemy import text, func
    
    tenant_id = current_user.tenant_id
    
    # Enforce Inter-run Concurrency Limit (max 2 active runs)
    # Use Postgres transaction-level advisory lock to prevent TOCTOU race conditions
    lock_id = hash(tenant_id.int) % (2**63 - 1)
    await db.execute(text("SELECT pg_advisory_xact_lock(:lock_id)").bindparams(lock_id=lock_id))
    
    stmt_active = select(func.count(EvaluationRun.id)).where(
        EvaluationRun.tenant_id == tenant_id,
        EvaluationRun.status.in_(["pending", "running"])
    )
    active_count = (await db.execute(stmt_active)).scalar()
    if active_count >= 2:
        raise HTTPException(
            status_code=429, 
            detail="Concurrency limit exceeded: You already have 2 active evaluation runs. Please wait for them to finish."
        )
        
    # Create the DB record
    db_run = EvaluationRun(
        tenant_id=tenant_id,
        agent_config_id=agent.id,
        name=run_in.name,
        run_config=run_in.run_config.model_dump(),
        status="pending"
    )
    db.add(db_run)
    await db.commit()
    await db.refresh(db_run)
    
    # Enqueue Celery task
    try:
        task = execute_evaluation_run.delay(str(db_run.id))
        db_run.celery_task_id = task.id
        await db.commit()
        await db.refresh(db_run)
    except Exception as e:
        logger.error(f"Failed to enqueue Celery task: {e}")
        db_run.status = "failed"
        db_run.error_message = f"Failed to enqueue task: {str(e)}"
        await db.commit()
        raise HTTPException(status_code=500, detail="Failed to enqueue evaluation task")
        
    return db_run

from fastapi import Query
import uuid

@router.get("/", response_model=List[EvaluationRunResponse])
async def list_evaluation_runs(
    current_user: CurrentUser,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db)
):
    """
    List evaluation runs for the current tenant.
    """
    stmt = select(EvaluationRun).where(
        EvaluationRun.tenant_id == current_user.tenant_id
    ).order_by(EvaluationRun.created_at.desc()).offset(skip).limit(limit)
    
    result = await db.execute(stmt)
    return result.scalars().all()

@router.get("/{run_id}", response_model=EvaluationRunResponse)
async def get_evaluation_run(
    run_id: UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db)
):
    """
    Get the status and summary stats of an evaluation run.
    """
    stmt = select(EvaluationRun).where(
        EvaluationRun.id == run_id,
        EvaluationRun.tenant_id == current_user.tenant_id
    )
    result = await db.execute(stmt)
    run = result.scalar_one_or_none()
    
    if not run:
        raise HTTPException(status_code=404, detail="Evaluation run not found")
        
    return run

@router.post("/{run_id}/cancel", response_model=EvaluationRunResponse)
async def cancel_evaluation_run(
    run_id: UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db)
):
    """
    Cancel a pending or running evaluation run.
    """
    stmt = select(EvaluationRun).where(
        EvaluationRun.id == run_id,
        EvaluationRun.tenant_id == current_user.tenant_id
    )
    result = await db.execute(stmt)
    run = result.scalar_one_or_none()
    
    if not run:
        raise HTTPException(status_code=404, detail="Evaluation run not found")
        
    if run.status in ["completed", "failed", "cancelled"]:
        raise HTTPException(status_code=400, detail="Cannot cancel a run that has already reached a terminal state.")
        
    run.status = "cancelled"
    await db.commit()
    await db.refresh(run)
    return run

@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_evaluation_run(
    run_id: UUID,
    current_user: CurrentAdmin,
    db: AsyncSession = Depends(get_db)
):
    """
    Delete an evaluation run. This physically removes all associated traces from S3
    before cascading the deletion in Postgres.
    """
    stmt = select(EvaluationRun).where(
        EvaluationRun.id == run_id,
        EvaluationRun.tenant_id == current_user.tenant_id
    )
    run = (await db.execute(stmt)).scalar_one_or_none()
    
    if not run:
        raise HTTPException(status_code=404, detail="Evaluation run not found")
        
    # Enqueue S3 cleanup task
    from backend.worker.tasks import delete_s3_traces
    try:
        delete_s3_traces.delay(str(run_id), str(current_user.tenant_id))
    except Exception as e:
        logger.error(f"Failed to enqueue S3 cleanup task for run {run_id}: {e}")
        raise HTTPException(
            status_code=500, 
            detail="Failed to enqueue cleanup task. Deletion aborted to prevent orphaned S3 data."
        )
        
    await db.delete(run)
    await db.commit()
    return None

@router.post("/{run_id}/stream-ticket")
async def get_stream_ticket(
    run_id: UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db)
):
    """
    Generate a short-lived ticket for authenticating SSE stream connections.
    """
    # Verify access
    stmt = select(EvaluationRun).where(
        EvaluationRun.id == run_id,
        EvaluationRun.tenant_id == current_user.tenant_id
    )
    result = await db.execute(stmt)
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Evaluation run not found")
        
    ticket = str(uuid.uuid4())
    redis = aioredis.from_url(settings.REDIS_PUBSUB_URL)
    try:
        # Store ticket mapping to run_id for 30 seconds
        await redis.setex(f"ticket:{ticket}", 30, str(run_id))
    finally:
        await redis.close()
        
    return {"ticket": ticket}

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
        # 1. Validate Ticket
        ticket_key = f"ticket:{ticket}"
        authorized_run_id = await redis.get(ticket_key)
        if not authorized_run_id or authorized_run_id.decode('utf-8') != str(run_id):
            raise HTTPException(status_code=401, detail="Invalid or expired stream ticket")
            
        # Consume the ticket so it can't be reused
        await redis.delete(ticket_key)
        
        async def event_generator(redis_client):
            sse_active_connections.add(1)
            
            # 2. Replay missed events if client is reconnecting
            if last_seq > 0:
                from backend.core.events import RedisEventPublisher
                replay_publisher = RedisEventPublisher()
                try:
                    missed_events = await replay_publisher.get_replay_events(str(run_id), last_seq)
                    for event_data in missed_events:
                        yield {"data": event_data}
                finally:
                    await replay_publisher.close()
            
            # 3. Subscribe to live events
            pubsub = redis_client.pubsub()
            channel_name = f"eval_stream:{run_id}"
            await pubsub.subscribe(channel_name)
            logger.info(f"Subscribed to {channel_name}")
            
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    if message and message["type"] == "message":
                        data = message["data"]
                        if isinstance(data, bytes):
                            data = data.decode("utf-8")
                        yield {"data": data}
                        
                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                pass
            finally:
                await pubsub.unsubscribe(channel_name)
                await redis_client.close()
                sse_active_connections.add(-1)
                logger.info(f"Unsubscribed from {channel_name}")
                
        return EventSourceResponse(event_generator(redis))
    except Exception:
        await redis.close()
        raise

@router.post("/{run_id}/baseline", response_model=EvaluationRunResponse)
async def set_golden_baseline(
    run_id: UUID,
    current_user: CurrentAdmin,
    db: AsyncSession = Depends(get_db)
):
    """
    Set an evaluation run as the Golden Baseline. This clears the baseline flag
    from all other runs belonging to the same agent configuration, ensuring only
    one baseline exists at a time (Atomic operation).
    """
    from sqlalchemy import update
    
    # 1. Verify the run exists and belongs to the tenant
    stmt = select(EvaluationRun).where(
        EvaluationRun.id == run_id,
        EvaluationRun.tenant_id == current_user.tenant_id
    )
    result = await db.execute(stmt)
    run = result.scalar_one_or_none()
    
    if not run:
        raise HTTPException(status_code=404, detail="Evaluation run not found")
        
    if run.status != "completed":
        raise HTTPException(status_code=400, detail="Only completed runs can be set as a baseline")
        
    if not run.avg_score or run.avg_score < 70:
        raise HTTPException(status_code=400, detail="Run must have a decent average score (>=70) to serve as a baseline")

    agent_id = run.agent_config_id

    # 2. Unset existing baseline for this agent
    unset_stmt = (
        update(EvaluationRun)
        .where(
            EvaluationRun.agent_config_id == agent_id,
            EvaluationRun.tenant_id == current_user.tenant_id,
            EvaluationRun.is_baseline == True
        )
        .values(is_baseline=False)
    )
    await db.execute(unset_stmt)
    
    # 3. Set the new baseline
    run.is_baseline = True
    
    # 4. Commit transaction atomically
    await db.commit()
    await db.refresh(run)
    
    return run

