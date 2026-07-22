import pytest
from unittest.mock import patch, AsyncMock

from backend.worker.tasks import _execute_evaluation_run_async
from backend.db.models import EvaluationRun

pytestmark = pytest.mark.asyncio

async def test_execute_evaluation_run_not_found(mocker):
    # Setup
    mock_logger = mocker.patch("backend.worker.tasks.logger")
    
    # Action
    await _execute_evaluation_run_async("00000000-0000-0000-0000-000000000000")
    
    # Result
    mock_logger.error.assert_called_once_with("EvaluationRun 00000000-0000-0000-0000-000000000000 not found.")
