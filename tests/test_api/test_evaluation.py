import pytest
from httpx import AsyncClient
import uuid

@pytest.mark.asyncio
async def test_create_evaluation_run_concurrency_limit(mocker):
    # Mock db
    mock_db = mocker.AsyncMock()
    mock_result = mocker.MagicMock()
    mock_db.execute.return_value = mock_result
    # Mock agent config exists
    mock_result.scalar_one_or_none.return_value = mocker.Mock(id=uuid.uuid4())
    # Mock active runs count >= 2
    mock_result.scalar.return_value = 2
    
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
