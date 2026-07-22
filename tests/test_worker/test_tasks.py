"""
Tests for Celery worker tasks.
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from uuid import UUID

from backend.worker.tasks import _execute_evaluation_run_async

pytestmark = pytest.mark.asyncio


async def test_execute_evaluation_run_not_found(mocker):
    """If run ID doesn't exist in DB, should log error and return."""
    mock_logger = mocker.patch("backend.worker.tasks.logger")

    # Mock the async session context manager
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_session
    mock_ctx.__aexit__.return_value = None

    with patch("backend.worker.tasks.AsyncSessionLocal", return_value=mock_ctx):
        await _execute_evaluation_run_async("00000000-0000-0000-0000-000000000000")

    mock_logger.error.assert_called_once_with(
        "EvaluationRun 00000000-0000-0000-0000-000000000000 not found."
    )


async def test_execute_evaluation_run_already_claimed(mocker):
    """If run is no longer pending, worker should abort gracefully."""
    mock_logger = mocker.patch("backend.worker.tasks.logger")

    # Mock session with a valid run but update returns 0 rows (already claimed)
    mock_session = AsyncMock()

    # First execute: SELECT for EvaluationRun
    mock_run = MagicMock()
    mock_run.id = UUID("00000000-0000-0000-0000-000000000001")
    mock_run.status = "running"

    mock_result_select = MagicMock()
    mock_result_select.scalar_one_or_none.return_value = mock_run

    mock_result_update = MagicMock()
    mock_result_update.rowcount = 0  # Already claimed

    mock_session.execute = AsyncMock(side_effect=[mock_result_select, mock_result_update])
    mock_session.commit = AsyncMock()

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_session
    mock_ctx.__aexit__.return_value = None

    with patch("backend.worker.tasks.AsyncSessionLocal", return_value=mock_ctx):
        await _execute_evaluation_run_async("00000000-0000-0000-0000-000000000001")

    # Should log warning about already-claimed run
    mock_logger.warning.assert_called_once()
    assert "no longer pending" in mock_logger.warning.call_args[0][0]
