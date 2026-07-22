import pytest
from unittest.mock import patch, AsyncMock
from typing import Dict, Any

from backend.agents.state import EvaluationState
from backend.agents.generator import generate_scenarios_node
from backend.agents.judge import judge_trace_node
from backend.schemas.evaluation import ScenarioGenerationResponse, ScenarioGenerationItem, JudgmentResponse

@pytest.mark.asyncio
async def test_generate_scenarios_node():
    state = {
        "config": {
            "scenario_count": 2,
            "target_description": "Test Agent",
            "target_system_prompt": "Prompt"
        },
        "total_cost_usd": 0.0,
        "turn_count": 0
    }
    
    mock_response = {
        "parsed": ScenarioGenerationResponse(
            scenarios=[
                ScenarioGenerationItem(title="S1", description="D1", expected_outcome="O1"),
                ScenarioGenerationItem(title="S2", description="D2", expected_outcome="O2")
            ]
        ),
        "cost_usd": 0.05
    }
    
    with patch("backend.agents.generator.llm_client.generate", return_value=mock_response):
        result = await generate_scenarios_node(state)
        
    assert "scenarios" in result
    assert len(result["scenarios"]) == 2
    assert result["scenarios"][0]["title"] == "S1"
    assert result["total_cost_usd"] == 0.05
    assert result["turn_count"] == 1

@pytest.mark.asyncio
async def test_judge_trace_node():
    state = {
        "scenarios": [
            {"id": "scen-1", "title": "S1", "description": "D1"}
        ],
        "traces": [
            {"scenario_id": "scen-1", "storage_key": "dummy", "raw_data": {"user": "hi", "bot": "hello"}}
        ],
        "total_cost_usd": 0.0,
        "turn_count": 0
    }
    
    mock_response = {
        "parsed": JudgmentResponse(
            score=95.5,
            reasoning="Did well",
            passed=True
        ),
        "cost_usd": 0.02
    }
    
    with patch("backend.agents.judge.llm_client.generate", return_value=mock_response), \
         patch("backend.agents.judge.s3_client.download_trace", new_callable=AsyncMock, return_value=b'{"interactions": [{"role": "user", "content": "hi"}, {"role": "target_agent", "content": "hello"}]}'):
        result = await judge_trace_node(state)
        
    assert "judgments" in result
    assert len(result["judgments"]) == 1
    judgment = result["judgments"][0]
    assert judgment["scenario_id"] == "scen-1"
    assert judgment["score"] == 95.5
    assert judgment["passed"] is True
    assert result["total_cost_usd"] == 0.02
    assert result["turn_count"] == 1
