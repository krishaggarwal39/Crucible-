from celery import Celery
from backend.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "crucible_worker",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["backend.worker.tasks", "backend.worker.beat"]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_ignore_result=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    beat_schedule={
        "sweep-zombie-runs-every-minute": {
            "task": "backend.worker.beat.sweep_zombie_runs",
            "schedule": 60.0,
        },
        "cleanup-orphaned-traces-every-10-minutes": {
            "task": "backend.worker.beat.cleanup_orphaned_traces",
            "schedule": 600.0,
        },
    }
)
