import asyncio

import pytest
from unittest.mock import patch, AsyncMock

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
    # Named `overall_score` to match the Judgment column and what the evolution
    # node reads; it used to be emitted as `score`, so evolution's score filter
    # silently never matched.
    assert judgment["overall_score"] == 95.5
    assert judgment["passed"] is True
    # raw_output is persisted for auditability and must not be empty.
    assert judgment["raw_output"]
    assert judgment["judge_model"]
    assert result["total_cost_usd"] == 0.02
    assert result["turn_count"] == 1


@pytest.mark.asyncio
async def test_judge_emits_sub_scores():
    """The Judgment table has three sub-score columns that were always NULL."""
    state = {
        "scenarios": [{"id": "scen-1", "title": "S1", "description": "D1"}],
        "traces": [{"scenario_id": "scen-1", "trace_id": "tr-1", "storage_key": "dummy"}],
        "config": {},
        "total_cost_usd": 0.0,
        "turn_count": 0,
    }
    mock_response = {
        "parsed": JudgmentResponse(
            score=80.0,
            reasoning="ok",
            passed=True,
            safety_score=90.0,
            correctness_score=70.0,
            instruction_following_score=85.0,
        ),
        "cost_usd": 0.01,
    }

    with patch("backend.agents.judge.llm_client.generate", return_value=mock_response), \
         patch("backend.agents.judge.s3_client.download_trace", new_callable=AsyncMock,
               return_value=b'{"interactions": []}'):
        result = await judge_trace_node(state)

    j = result["judgments"][0]
    assert j["safety_score"] == 90.0
    assert j["correctness_score"] == 70.0
    assert j["instruction_following_score"] == 85.0
    assert j["trace_id"] == "tr-1"


@pytest.mark.asyncio
async def test_judge_semaphore_bounds_concurrent_llm_calls(mocker):
    """
    The semaphore must actually guard the LLM call.

    It previously wrapped only two dict lookups, leaving the download and the
    LLM request completely unbounded.
    """
    mocker.patch("backend.agents.judge.settings.NODE_CONCURRENCY_LIMIT", 2)
    mocker.patch(
        "backend.agents.judge.s3_client.download_trace",
        new_callable=AsyncMock,
        return_value=b'{"interactions": []}',
    )

    in_flight = 0
    peak = 0

    async def fake_generate(*args, **kwargs):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return {
            "parsed": JudgmentResponse(score=50.0, reasoning="r", passed=False),
            "cost_usd": 0.0,
        }

    mocker.patch("backend.agents.judge.llm_client.generate", side_effect=fake_generate)

    state = {
        "scenarios": [{"id": f"s{i}", "title": "t", "description": "d"} for i in range(8)],
        "traces": [
            {"scenario_id": f"s{i}", "trace_id": f"tr{i}", "storage_key": f"k{i}"}
            for i in range(8)
        ],
        "config": {},
        "total_cost_usd": 0.0,
        "turn_count": 0,
    }

    result = await judge_trace_node(state)

    assert len(result["judgments"]) == 8
    assert peak <= 2, f"semaphore did not bound concurrency (peak={peak})"


@pytest.mark.asyncio
async def test_judge_stops_when_budget_exhausted(mocker):
    """Budget is only re-checked on graph edges, so the node must self-limit."""
    mocker.patch("backend.agents.judge.settings.NODE_CONCURRENCY_LIMIT", 2)
    mocker.patch(
        "backend.agents.judge.s3_client.download_trace",
        new_callable=AsyncMock,
        return_value=b'{"interactions": []}',
    )
    mocker.patch(
        "backend.agents.judge.llm_client.generate",
        new_callable=AsyncMock,
        return_value={
            "parsed": JudgmentResponse(score=50.0, reasoning="r", passed=False),
            "cost_usd": 1.0,
        },
    )

    state = {
        "scenarios": [{"id": f"s{i}", "title": "t", "description": "d"} for i in range(8)],
        "traces": [
            {"scenario_id": f"s{i}", "trace_id": f"tr{i}", "storage_key": f"k{i}"}
            for i in range(8)
        ],
        "config": {"max_budget_usd": 2.0},
        "total_cost_usd": 0.0,
        "turn_count": 0,
    }

    result = await judge_trace_node(state)

    assert result["total_cost_usd"] <= 2.0 + 1e-9 or result["total_cost_usd"] == 2.0
    skipped = [
        j for j in result["judgments"]
        if "BUDGET EXHAUSTED" in (j.get("reasoning") or "")
    ]
    assert skipped, "expected remaining traces to be marked as unjudged"
    assert any("Budget limit reached" in e for e in result.get("errors", []))
