"""
Task dispatch for the API process.

The API only needs to *enqueue* work, but importing `backend.worker.tasks`
pulls in the entire agent pipeline — litellm, langgraph, qdrant-client,
aiobotocore, tiktoken, numpy — and executes module-level side effects that
construct an LLM client, three S3 clients and a Qdrant client (which performs
network I/O on construction) inside the web process.

Celery can dispatch by task *name* without importing the implementation, so this
module keeps the API dependent only on the broker.
"""

import logging

from celery.result import AsyncResult

from backend.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

EXECUTE_EVALUATION_RUN = "backend.worker.tasks.execute_evaluation_run"
DELETE_S3_TRACES = "backend.worker.tasks.delete_s3_traces"


def enqueue_evaluation_run(run_id: str) -> str:
    """Queue an evaluation run and return the Celery task id."""
    result = celery_app.send_task(EXECUTE_EVALUATION_RUN, args=[str(run_id)])
    return result.id


def enqueue_s3_trace_cleanup(run_id: str, tenant_id: str) -> str:
    """Queue deletion of a run's trace blobs and return the Celery task id."""
    result = celery_app.send_task(
        DELETE_S3_TRACES, args=[str(run_id), str(tenant_id)]
    )
    return result.id


def revoke_task(task_id: str | None, terminate: bool = True) -> bool:
    """
    Revoke a queued or running Celery task.

    EvaluationRun.celery_task_id is documented as being stored "so we can revoke
    the task if the user cancels", but nothing ever called revoke. Without this,
    a pending task still starts (and then aborts on the status check), and a
    running task only stops at the next 5-second cancellation poll between graph
    nodes.

    Returns True if a revoke was dispatched. Failures are logged and swallowed:
    the DB status change is the source of truth, and the worker also polls it.
    """
    if not task_id:
        return False
    try:
        AsyncResult(task_id, app=celery_app).revoke(terminate=terminate, signal="SIGTERM")
        return True
    except Exception as e:
        logger.warning(f"Could not revoke Celery task {task_id}: {e}")
        return False
