import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from backend.agents.orchestrator import build_evaluation_graph
from backend.schemas.evaluation import ScenarioGenerationResponse, ScenarioGenerationItem, JudgmentResponse

@pytest.fixture
def mock_llm():
    with patch("backend.agents.generator.llm_client.generate") as mock_gen, \
         patch("backend.agents.judge.llm_client.generate") as mock_judge, \
         patch("backend.agents.simulator.upload_trace_with_retry", return_value="dummy/s3/path"), \
         patch("backend.agents.judge.s3_client.download_trace", return_value=b'{"interactions": []}'):
        
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
        # generator: 0.01, simulator: 0 (no mock cost added), judge: 0.02 = 0.03 (analyze_drift_node returns empty dict now)
        assert abs(result["total_cost_usd"] - 0.03) < 1e-5
        # 1 from generate + 1 from simulate (1 scenario) + 1 from judge = 3 turns, wait... generate doesn't add turn_count?
        # generator returns turn_count=1, simulator adds turn_count=1 (len(scenarios)), judge adds turn_count=1 (len(traces))
        # Total turns = 3. Wait, analyze_drift_node doesn't return anything.
        assert result["turn_count"] == 3

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


