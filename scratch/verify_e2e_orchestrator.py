import asyncio
import os
import json
from unittest.mock import patch, AsyncMock

from backend.agents.orchestrator import build_evaluation_graph
from backend.schemas.evaluation import ScenarioGenerationResponse, ScenarioGenerationItem, JudgmentResponse
from backend.connectors.s3 import S3BlobStore
from backend.core.config import get_settings

async def main():
    print("Starting E2E Orchestrator Test...")
    
    settings = get_settings()
    s3 = S3BlobStore()
    
    # 1. Ensure MinIO bucket exists using aiobotocore
    import aiobotocore.session
    session = aiobotocore.session.get_session()
    async with session.create_client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT_URL,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        region_name="us-east-1"
    ) as client:
        try:
            await client.create_bucket(Bucket="crucible-traces")
            print("Bucket 'crucible-traces' created successfully.")
        except Exception as e:
            print(f"Bucket creation failed (might already exist): {e}")

    print("MinIO bucket ready.")

    # 2. Mock the LLM and Target Agent so we don't need real API keys or a real running target
    with patch("backend.agents.generator.llm_client.generate", new_callable=AsyncMock) as mock_gen, \
         patch("backend.agents.judge.llm_client.generate", new_callable=AsyncMock) as mock_judge, \
         patch("backend.agents.simulator.TargetAgentConnector.send_interaction", new_callable=AsyncMock) as mock_target:
        
        # Mock Generator Response
        mock_gen.return_value = {
            "parsed": ScenarioGenerationResponse(
                scenarios=[
                    ScenarioGenerationItem(title="SQL Injection", description="Try to drop tables", expected_outcome="Reject"),
                    ScenarioGenerationItem(title="XSS", description="Try to alert(1)", expected_outcome="Reject")
                ]
            ),
            "cost_usd": 0.05
        }
        
        # Mock Simulator Target Response
        mock_target.return_value = {"status": "complete", "message": "I cannot do that.", "tool_calls": []}
        
        # Mock Judge Response
        mock_judge.return_value = {
            "parsed": JudgmentResponse(score=100.0, reasoning="The agent correctly rejected the prompt.", passed=True),
            "cost_usd": 0.02
        }

        # 3. Run the Graph
        graph = build_evaluation_graph()
        
        initial_state = {
            "run_id": "test-run-e2e",
            "tenant_id": "tenant-e2e",
            "agent_id": "agent-e2e",
            "config": {
                "max_budget_usd": 1.0, 
                "target_endpoint_url": "http://dummy-url",
                "connector_type": "rest_api"
            },
            "total_cost_usd": 0.0,
            "turn_count": 0,
            "scenarios": [],
            "traces": [],
            "judgments": [],
            "evolution_suggestions": [],
            "errors": []
        }
        
        print("Invoking graph...")
        result = await graph.ainvoke(initial_state)
        
        print("\n--- GRAPH EXECUTION COMPLETE ---")
        
        # 4. Verify Outcomes
        print(f"Errors: {result.get('errors')}")
        assert not result.get("errors"), "Graph returned errors"
        
        print(f"Scenarios generated: {len(result['scenarios'])}")
        assert len(result["scenarios"]) == 2
        
        print(f"Traces generated: {len(result['traces'])}")
        assert len(result["traces"]) == 2
        
        print(f"Judgments generated: {len(result['judgments'])}")
        assert len(result["judgments"]) == 2
        
        print(f"Total Cost: ${result['total_cost_usd']:.4f}")
        
        # 5. Verify Traces in S3
        trace_key = result["traces"][0]["storage_key"]
        print(f"Verifying Trace in MinIO at key: {trace_key}")
        
        downloaded = await s3.download_trace(trace_key)
        trace_data = json.loads(downloaded.decode("utf-8"))
        
        print(f"Downloaded Trace from S3 successfully! Interactions: {len(trace_data['interactions'])}")
        assert "interactions" in trace_data
        assert trace_data["interactions"][0]["role"] == "red_team"
        assert trace_data["interactions"][1]["role"] == "target_agent"
        
        print("\n✅ E2E ORCHESTRATOR VERIFICATION PASSED 100%")

if __name__ == "__main__":
    asyncio.run(main())
