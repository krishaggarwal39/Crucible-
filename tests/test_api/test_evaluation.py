import pytest
import uuid

@pytest.mark.asyncio
async def test_create_evaluation_run_concurrency_limit(mocker):
    # Mock db
    mock_db = mocker.AsyncMock()
    mock_result = mocker.MagicMock()
    mock_db.execute.return_value = mock_result
    # Mock agent config exists
    mock_result.scalar_one_or_none.return_value = mocker.Mock(id=uuid.uuid4())
    # Active run count is read via db.scalar()
    mock_db.scalar.return_value = 2

    from backend.api.routes.evaluation import create_evaluation_run
    from backend.schemas.evaluation import EvaluationRunCreate
    from fastapi import HTTPException

    run_in = EvaluationRunCreate(name="test", agent_config_id=uuid.uuid4(), run_config={})
    user = mocker.Mock(tenant_id=uuid.uuid4())

    with pytest.raises(HTTPException) as exc:
        await create_evaluation_run(run_in=run_in, current_user=user, db=mock_db)

    assert exc.value.status_code == 429
    assert "Concurrency limit exceeded" in exc.value.detail


@pytest.mark.asyncio
async def test_create_evaluation_run_rejects_unknown_fields():
    """
    The dashboard used to POST `dataset_id`, which the API silently discarded.
    Unknown keys must now be rejected instead of quietly ignored.
    """
    from pydantic import ValidationError

    from backend.schemas.evaluation import EvaluationRunCreate

    with pytest.raises(ValidationError):
        EvaluationRunCreate(
            name="test", agent_config_id=uuid.uuid4(), dataset_id="ds-1234"
        )


@pytest.mark.asyncio
async def test_cancel_revokes_the_celery_task(mocker):
    """
    celery_task_id is stored specifically so a cancel can revoke the task, but
    revoke was never called.
    """
    mock_db = mocker.AsyncMock()
    mock_result = mocker.MagicMock()
    mock_db.execute.return_value = mock_result
    run_mock = mocker.Mock(status="running", celery_task_id="task-abc")
    mock_result.scalar_one_or_none.return_value = run_mock

    revoke = mocker.patch("backend.api.routes.evaluation.revoke_task")

    from backend.api.routes.evaluation import cancel_evaluation_run

    user = mocker.Mock(tenant_id=uuid.uuid4())
    await cancel_evaluation_run(run_id=uuid.uuid4(), current_user=user, db=mock_db)

    assert run_mock.status == "cancelled"
    revoke.assert_called_once_with("task-abc")

@pytest.mark.asyncio
async def test_cancel_evaluation_run_terminal_state(mocker):
    # Mock db
    mock_db = mocker.AsyncMock()
    mock_result = mocker.MagicMock()
    mock_db.execute.return_value = mock_result
    
    # Mock run exists but is already completed
    run_mock = mocker.Mock(status="completed")
    mock_result.scalar_one_or_none.return_value = run_mock
    
    from backend.api.routes.evaluation import cancel_evaluation_run
    from fastapi import HTTPException
    
    user = mocker.Mock(tenant_id=uuid.uuid4())
    
    with pytest.raises(HTTPException) as exc:
        await cancel_evaluation_run(run_id=uuid.uuid4(), current_user=user, db=mock_db)
        
    assert exc.value.status_code == 400
    assert "Cannot cancel a run that has already reached a terminal state" in exc.value.detail
