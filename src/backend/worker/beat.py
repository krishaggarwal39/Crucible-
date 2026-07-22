import logging
from datetime import datetime, timezone, timedelta
from celery import shared_task
from sqlalchemy.future import select

from backend.db.session import AsyncSessionLocal
from backend.db.models import EvaluationRun
from backend.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

async def _sweep_zombie_runs_async():
    """
    Finds running tasks where the last heartbeat was more than 5 minutes ago
    and marks them as failed atomically.
    """
    from sqlalchemy import update
    timeout_secs = settings.ZOMBIE_TIMEOUT_SECONDS
    logger.info(f"Sweeping for zombie evaluation runs (timeout: {timeout_secs}s)...")
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=timeout_secs)
    
    async with AsyncSessionLocal() as session:
        # Atomic update for runs that had a heartbeat but timed out
        stmt1 = (
            update(EvaluationRun)
            .where(
                EvaluationRun.status == "running",
                EvaluationRun.last_heartbeat_at < cutoff
            )
            .values(
                status="failed",
                error_message="Task failed: Worker died or heartbeat lost."
            )
        )
        result1 = await session.execute(stmt1)
        
        # Atomic update for runs that started but never heartbeated
        stmt2 = (
            update(EvaluationRun)
            .where(
                EvaluationRun.status == "running",
                EvaluationRun.last_heartbeat_at.is_(None),
                EvaluationRun.started_at < cutoff
            )
            .values(
                status="failed",
                error_message=f"Task failed: No heartbeat received within {timeout_secs} seconds."
            )
        )
        result2 = await session.execute(stmt2)
        
        if result1.rowcount > 0 or result2.rowcount > 0:
            await session.commit()
            logger.info(f"Marked {result1.rowcount + result2.rowcount} zombie runs as failed.")


async def _cleanup_orphaned_traces_async():
    """
    Finds failed/cancelled runs older than 1 hour that still have S3 traces
    (i.e., the normal cleanup task was never enqueued or failed) and removes them.
    
    Safety: Only targets runs where total_scenarios == 0 (meaning _persist_final_state
    never completed successfully, so there's no judgment data referencing these traces).
    """
    from backend.connectors.s3 import S3BlobStore

    now = datetime.now(timezone.utc)
    orphan_cutoff = now - timedelta(hours=1)

    async with AsyncSessionLocal() as session:
        stmt = (
            select(EvaluationRun)
            .where(
                EvaluationRun.status.in_(["failed", "cancelled"]),
                EvaluationRun.updated_at < orphan_cutoff,
                EvaluationRun.total_scenarios == 0,  # Never persisted properly
            )
            .limit(10)  # Process in small batches to avoid long locks
        )
        result = await session.execute(stmt)
        orphan_runs = result.scalars().all()

    if not orphan_runs:
        return

    logger.info(f"Found {len(orphan_runs)} orphan runs to clean up S3 traces for.")
    s3 = S3BlobStore()
    try:
        for run in orphan_runs:
            prefix = f"tenants/{run.tenant_id}/runs/{run.id}/"
            try:
                await s3.delete_prefix(prefix)
                logger.info(f"Cleaned up orphaned S3 traces for run {run.id}")
            except Exception as e:
                logger.warning(f"Failed to clean S3 traces for run {run.id}: {e}")
    finally:
        await s3.close()


@shared_task(bind=True)
def sweep_zombie_runs(self):
    """
    Celery Beat task to sweep zombie evaluation runs.
    """
    from backend.worker.utils import run_async_graph
    run_async_graph(_sweep_zombie_runs_async())


@shared_task(bind=True)
def cleanup_orphaned_traces(self):
    """
    Celery Beat task to clean up S3 traces for runs that failed without proper cleanup.
    Runs every 10 minutes.
    """
    from backend.worker.utils import run_async_graph
    run_async_graph(_cleanup_orphaned_traces_async())
