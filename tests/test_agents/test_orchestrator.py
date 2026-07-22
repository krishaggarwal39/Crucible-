import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from backend.agents.orchestrator import build_evaluation_graph
from backend.schemas.evaluation import ScenarioGenerationResponse, ScenarioGenerationItem, JudgmentResponse

@pytest.fixture
def mock_llm():
    with patch("backend.agents.generator.llm_client.generate") as mock_gen, \
         patch("backend.agents.judge.llm_client.generate") as mock_judge, \
         patch("backend.agents.simulator.upload_trace_with_retry", return_value="dummy/s3/path"), \
         patch("backend.agents.judge.s3_client.download_trace", return_value=b'{"interactions": []}'), \
         patch("backend.agents.analyzer.vector_db.init_collection", new_callable=AsyncMock), \
         patch("backend.agents.analyzer.s3_client.download_trace", new_callable=AsyncMock, return_value=b'{"interactions": []}'), \
         patch("backend.agents.analyzer.llm_client.generate", new_callable=AsyncMock, return_value={"content": "summary", "cost_usd": 0.001}), \
         patch("backend.agents.analyzer.llm_client.embed", new_callable=AsyncMock, return_value={"vector": [0.1] * 1536, "cost_usd": 0.0001}), \
         patch("backend.agents.analyzer.vector_db.upsert_trace_embedding", new_callable=AsyncMock), \
         patch("backend.agents.analyzer._resolve_baseline", new_callable=AsyncMock, return_value=None), \
         patch("backend.agents.evolution.llm_client.generate", new_callable=AsyncMock) as mock_evo:
        
        mock_gen.return_value = {
            "parsed": ScenarioGenerationResponse(
                scenarios=[ScenarioGenerationItem(title="S1", description="D1", expected_outcome="O1")]
            ),
            "cost_usd": 0.01
        }
        
        mock_judge.return_value = {
            "parsed": JudgmentResponse(score=85.0, reasoning="Good", passed=True),
            "cost_usd": 0.02
        }
        yield mock_gen, mock_judge

@pytest.mark.asyncio
async def test_graph_compiles_and_runs(mock_llm):
    graph = build_evaluation_graph()
    
    with patch("backend.agents.simulator.TargetAgentConnector.send_interaction", new_callable=AsyncMock, return_value={"status": "ok"}):
        initial_state = {
            "run_id": "00000000-0000-0000-0000-000000000001",
            "tenant_id": "00000000-0000-0000-0000-000000000002",
            "agent_id": "00000000-0000-0000-0000-000000000003",
            "config": {"max_budget_usd": 1.0, "target_endpoint_url": "http://dummy", "connector_type": "rest_api"},
            "total_cost_usd": 0.0,
            "turn_count": 0,
            "scenarios": [],
            "traces": [],
            "judgments": [],
            "evolution_suggestions": [],
            "errors": []
        }
        
        result = await graph.ainvoke(initial_state)
        
        assert "scenarios" in result
        assert len(result["scenarios"]) == 1
        assert "traces" in result
        assert "judgments" in result
        # generator: 0.01, simulator: 0, judge: 0.02, analyzer: 0.0011 (summary + embed)
        assert result["total_cost_usd"] > 0.03
        # generator: 1, simulator: 1, judge: 1 = 3 turns minimum
        assert result["turn_count"] >= 3

@pytest.mark.asyncio
async def test_graph_halts_on_budget_exceeded(mock_llm):
    graph = build_evaluation_graph()
    
    initial_state = {
        "run_id": "00000000-0000-0000-0000-000000000001",
        "tenant_id": "00000000-0000-0000-0000-000000000002",
        "agent_id": "00000000-0000-0000-0000-000000000003",
        "config": {"max_budget_usd": 0.005, "target_endpoint_url": "http://dummy", "connector_type": "rest_api"},
        "total_cost_usd": 0.0,
        "turn_count": 0,
        "scenarios": [],
        "traces": [],
        "judgments": [],
        "evolution_suggestions": [],
        "errors": []
    }
    
    result = await graph.ainvoke(initial_state)
    
    assert len(result.get("traces", [])) == 0
    assert result["total_cost_usd"] == 0.01
    assert result["turn_count"] == 1


