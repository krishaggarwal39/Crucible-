import asyncio
import json
import os
import sys
from httpx import AsyncClient
from uuid import uuid4

# Add the src folder to the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from backend.db.session import AsyncSessionLocal
from backend.db.models import AgentConfig, Tenant, EvaluationRun, Scenario, TraceMetadata, Judgment

async def setup_test_data():
    """Sets up a tenant and agent config for testing."""
    async with AsyncSessionLocal() as session:
        tenant_id = uuid4()
        tenant = Tenant(id=tenant_id, name="Golden Path Test Tenant", slug=f"golden-path-{uuid4().hex[:8]}")
        session.add(tenant)
        
        agent_id = uuid4()
        agent = AgentConfig(
            id=agent_id, 
            tenant_id=tenant_id, 
            name="Test Agent", 
            endpoint_url="http://localhost:8000/mock", 
            connector_type="rest_api",
            system_prompt="You are a helpful assistant."
        )
        session.add(agent)
        
        await session.commit()
        return tenant_id, agent_id

async def verify_db_state(run_id: str):
    """Verifies that the DB contains the correct records after a completed run."""
    async with AsyncSessionLocal() as session:
        # Check run
        from sqlalchemy import select
        stmt = select(EvaluationRun).where(EvaluationRun.id == run_id)
        result = await session.execute(stmt)
        run = result.scalar_one_or_none()
        
        assert run is not None, "Evaluation run not found in DB"
        assert run.status == "completed", f"Run status is {run.status}, expected completed"
        assert run.passed_scenarios + run.failed_scenarios > 0, "No scenarios were evaluated"
        
        # Check scenarios
        stmt = select(Scenario).where(Scenario.tenant_id == run.tenant_id)
        result = await session.execute(stmt)
        scenarios = result.scalars().all()
        assert len(scenarios) > 0, "No scenarios persisted"
        
        # Check traces
        stmt = select(TraceMetadata).where(TraceMetadata.evaluation_run_id == run.id)
        result = await session.execute(stmt)
        traces = result.scalars().all()
        assert len(traces) == len(scenarios), f"Expected {len(scenarios)} traces, got {len(traces)}"
        
        # Check judgments
        stmt = select(Judgment).where(Judgment.trace_id.in_([t.id for t in traces]))
        result = await session.execute(stmt)
        judgments = result.scalars().all()
        assert len(judgments) == len(traces), f"Expected {len(traces)} judgments, got {len(judgments)}"
        
        return run

from unittest.mock import AsyncMock, patch

async def main():
    print("Setting up test data...")
    tenant_id, agent_id = await setup_test_data()
    
    # We will use the FastAPI test client or just directly call the Celery task
    # To test SSE properly, we actually need the API running. 
    # For this automated script, we'll trigger the celery task synchronously or mock the API.
    # Let's trigger via the API if it's running, or we can just run the worker async task directly.
    
    # Since we want to test SSE, we should spin up the FastAPI app using httpx.ASGITransport
    from httpx import ASGITransport
    from backend.main import app
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        print("Triggering evaluation run...")
        response = await client.post(
            "/api/v1/evaluations/", 
            json={
                "agent_config_id": str(agent_id),
                "name": "Golden Path Eval",
                "run_config": {}
            }
        )
        assert response.status_code == 201, f"Failed to create run: {response.text}"
        run_data = response.json()
        run_id = run_data["id"]
        print(f"Run ID: {run_id}")
        
        # We also need the celery worker to process this.
        # But `tasks.execute_evaluation_run.delay()` was called, which sends to Redis.
        # If the worker isn't running in a separate process, it will just sit in the queue.
        # For this test, we can manually invoke the async function to simulate the worker.
        from backend.worker.tasks import _execute_evaluation_run_async
        
        # Create a background task for the worker so we can stream at the same time
        worker_task = asyncio.create_task(_execute_evaluation_run_async(run_id))
        
        print("Streaming SSE events...")
        # Now stream from the SSE endpoint
        last_seq = 0
        events_received = 0
        terminal_received = False
        
        async with client.stream("GET", f"/api/v1/evaluations/{run_id}/stream") as stream_response:
            assert stream_response.status_code == 200, "SSE stream failed"
            async for line in stream_response.aiter_lines():
                if line.startswith("data: "):
                    data_str = line[len("data: "):]
                    event = json.loads(data_str)
                    print(f"Received event: {event['current_node']} (seq={event['seq_num']})")
                    
                    assert event["seq_num"] > last_seq, "Sequence numbers are not monotonically increasing"
                    last_seq = event["seq_num"]
                    events_received += 1
                    
                    if event["status"] in ["completed", "failed"]:
                        terminal_received = True
                        if event["status"] == "failed":
                            print(f"Run failed: {event.get('error_message')}")
                        break

        # Wait for worker to officially finish its final finally block
        await worker_task
        
        assert terminal_received, "Never received terminal SSE event"
        assert events_received >= 3, "Received too few events"
        
        print("Verifying DB state...")
        await verify_db_state(run_id)
        
        print("Golden Path test PASSED!")

if __name__ == "__main__":
    from unittest.mock import MagicMock
    
    # Create a mock graph that yields fake states
    mock_graph = MagicMock()
    
    async def mock_astream(*args, **kwargs):
        yield {"generate_scenarios": {"turn_count": 1, "total_cost_usd": 0.01, "scenarios": [{"id": "s1", "title": "Test Scenario", "description": "Mock description", "expected_outcome": "Mock outcome"}]}}
        yield {"execute_scenarios": {"turn_count": 2, "total_cost_usd": 0.02, "traces": [{"trace_id": str(uuid4()), "storage_key": "mock/key.json", "turn_count": 2, "cost_usd": 0.01}]}}
        yield {"judge_scenarios": {"turn_count": 3, "total_cost_usd": 0.03, "judgments": [{"trace_id": "t1", "passed": True, "score": 1.0, "reasoning": "Mock reason"}]}}
        
    mock_graph.astream = mock_astream
    
    with patch("backend.worker.tasks.build_evaluation_graph", return_value=mock_graph):
        asyncio.run(main())
