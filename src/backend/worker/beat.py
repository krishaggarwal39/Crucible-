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

@shared_task(bind=True)
def sweep_zombie_runs(self):
    """
    Celery Beat task to sweep zombie evaluation runs.
    """
    from backend.worker.utils import run_async_graph
    run_async_graph(_sweep_zombie_runs_async())
