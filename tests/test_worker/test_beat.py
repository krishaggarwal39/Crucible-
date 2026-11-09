"""
Tests for the Celery Beat zombie sweeper task.
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from backend.worker.beat import _sweep_zombie_runs_async


@pytest.mark.asyncio
async def test_sweep_zombie_runs_marks_stale_heartbeats():
    """Runs with old heartbeats should be marked as failed."""
    with patch("backend.worker.beat.AsyncSessionLocal") as mock_factory:
        mock_session = AsyncMock()
        mock_context = AsyncMock()
        mock_context.__aenter__.return_value = mock_session
        mock_context.__aexit__.return_value = None
        mock_factory.return_value = mock_context

        # Mock the two update statements
        mock_result1 = MagicMock()
        mock_result1.rowcount = 2
        mock_result2 = MagicMock()
        mock_result2.rowcount = 1
        mock_session.execute = AsyncMock(side_effect=[mock_result1, mock_result2])

        await _sweep_zombie_runs_async()

        # Should have committed since rowcount > 0
        mock_session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_sweep_zombie_runs_no_zombies():
    """If no zombie runs found, should not commit."""
    with patch("backend.worker.beat.AsyncSessionLocal") as mock_factory:
        mock_session = AsyncMock()
        mock_context = AsyncMock()
        mock_context.__aenter__.return_value = mock_session
        mock_context.__aexit__.return_value = None
        mock_factory.return_value = mock_context

        mock_result1 = MagicMock()
        mock_result1.rowcount = 0
        mock_result2 = MagicMock()
        mock_result2.rowcount = 0
        mock_session.execute = AsyncMock(side_effect=[mock_result1, mock_result2])

        await _sweep_zombie_runs_async()

        mock_session.commit.assert_not_called()
