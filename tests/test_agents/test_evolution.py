"""
Tests for the Evolution node (system prompt improvement suggestions).
"""

import pytest
from unittest.mock import patch, AsyncMock

from backend.agents.evolution import evolution_node, EvolutionSuggestion


@pytest.mark.asyncio
async def test_evolution_node_no_failures():
    """If all judgments passed with high scores, no suggestions should be generated."""
    state = {
        "judgments": [
            {"overall_score": 95, "passed": True, "reasoning": "Great"},
            {"overall_score": 88, "passed": True, "reasoning": "Good"},
        ],
        "config": {"target_system_prompt": "You are helpful"},
    }
    result = await evolution_node(state)
    assert result["evolution_suggestions"] == []


@pytest.mark.asyncio
async def test_evolution_node_with_failures():
    """Failed judgments should trigger an LLM call for improvement suggestions."""
    state = {
        "judgments": [
            {"overall_score": 45, "passed": False, "reasoning": "Agent leaked data", "trace_id": "t1"},
            {"overall_score": 30, "passed": False, "reasoning": "Agent ignored instructions", "trace_id": "t2"},
            {"overall_score": 90, "passed": True, "reasoning": "Fine"},
        ],
        "config": {
            "target_system_prompt": "You are a helpful assistant",
            "target_tools": [],
        },
    }

    mock_suggestion = EvolutionSuggestion(
        failure_pattern_synthesis="Agent fails to enforce data boundaries",
        suggested_system_prompt="You are a helpful assistant. Never reveal internal data.",
        suggested_tool_changes=None,
    )

    with patch("backend.agents.evolution.llm_client.generate", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = {"parsed": mock_suggestion, "cost_usd": 0.03}

        result = await evolution_node(state)

    assert len(result["evolution_suggestions"]) == 1
    suggestion = result["evolution_suggestions"][0]
    assert suggestion["status"] == "pending_review"
    assert "data boundaries" in suggestion["failure_pattern"]
    assert result["total_cost_usd"] == 0.03


@pytest.mark.asyncio
async def test_evolution_node_llm_failure():
    """If LLM call fails, should return an error instead of crashing."""
    state = {
        "judgments": [
            {"overall_score": 20, "passed": False, "reasoning": "Bad", "trace_id": "t1"},
        ],
        "config": {"target_system_prompt": "Prompt"},
    }

    with patch("backend.agents.evolution.llm_client.generate", new_callable=AsyncMock) as mock_gen:
        mock_gen.side_effect = Exception("API timeout")

        result = await evolution_node(state)

    assert "errors" in result
    assert "Evolution node error" in result["errors"][0]


@pytest.mark.asyncio
async def test_evolution_node_limits_to_5_worst():
    """Should only send the 5 worst failures to the LLM."""
    state = {
        "judgments": [
            {"overall_score": i * 10, "passed": False, "reasoning": f"Fail {i}", "trace_id": f"t{i}"}
            for i in range(8)
        ],
        "config": {"target_system_prompt": "Prompt", "target_tools": []},
    }

    from backend.agents.evolution import EvolutionSuggestion

    mock_suggestion = EvolutionSuggestion(
        failure_pattern_synthesis="Pattern",
        suggested_system_prompt="New prompt",
        suggested_tool_changes=None,
    )

    with patch("backend.agents.evolution.llm_client.generate", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = {"parsed": mock_suggestion, "cost_usd": 0.01}
        await evolution_node(state)

        # Verify the prompt sent to the LLM contains the worst failures
        call_args = mock_gen.call_args
        messages = call_args[1].get("messages") or call_args[0][1]
        prompt_text = messages[0]["content"]
        # 5 worst: scores 0, 10, 20, 30, 40
        assert "Fail 0" in prompt_text
        assert "Fail 4" in prompt_text
        # Score 50+ should not appear (indices 5, 6, 7)
        assert "Fail 5" not in prompt_text
