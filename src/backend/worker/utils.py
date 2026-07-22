import asyncio
import logging
from typing import Any, Coroutine

logger = logging.getLogger(__name__)

_worker_loop = None

def get_worker_loop():
    global _worker_loop
    if _worker_loop is None or _worker_loop.is_closed():
        _worker_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_worker_loop)
    return _worker_loop

def run_async_graph(coro: Coroutine[Any, Any, Any]) -> Any:
    """
    Synchronous wrapper to execute an asynchronous LangGraph execution block.
    This encapsulates the async/sync bridge for Celery workers by reusing a single
    persistent event loop per worker process, avoiding SQLAlchemy connection pool 
    'attached to a different loop' errors.
    """
    try:
        loop = get_worker_loop()
        return loop.run_until_complete(coro)
    except Exception as e:
        logger.error(f"Error executing async graph: {e}", exc_info=True)
        raise
