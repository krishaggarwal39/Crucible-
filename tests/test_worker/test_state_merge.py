"""
Tests for the graph-state accumulation the worker performs while streaming.

Regression target: the worker consumes stream_mode="updates" and previously
merged each node's delta with plain dict.update(). That OVERWRITES, so the
additive reducers declared on EvaluationState were ignored and the cost/turn
count published to clients equalled only the *last* node's delta.

Measured before the fix, with a fully mocked graph run:
    graph-accumulated total_cost_usd = 0.118   turn_count = 7
    worker-reported total_cost_usd   = 0.030   turn_count = 3
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from backend.agents.orchestrator import build_evaluation_graph
from backend.schemas.evaluation import (
    EvolutionSuggestionResponse,
    JudgmentResponse,
    ScenarioGenerationItem,
    ScenarioGenerationResponse,
)
from backend.worker.tasks import _merge_state_update, _tally


class TestMergeStateUpdate:
    def test_numeric_fields_accumulate(self):
        state = {"total_cost_usd": 0.01, "turn_count": 1}
        _merge_state_update(state, {"total_cost_usd": 0.02, "turn_count": 3})
        assert state["total_cost_usd"] == pytest.approx(0.03)
        assert state["turn_count"] == 4

    def test_list_fields_accumulate(self):
        state = {"judgments": [{"a": 1}], "errors": ["first"]}
        _merge_state_update(state, {"judgments": [{"b": 2}], "errors": ["second"]})
        assert len(state["judgments"]) == 2
        assert state["errors"] == ["first", "second"]

    def test_scalar_fields_are_replaced(self):
        state = {"drift_profile": {"status": "old"}}
        _merge_state_update(state, {"drift_profile": {"status": "new"}})
        assert state["drift_profile"] == {"status": "new"}

    def test_missing_keys_initialise(self):
        state = {}
        _merge_state_update(state, {"total_cost_usd": 0.5, "traces": [1]})
        assert state["total_cost_usd"] == 0.5
        assert state["traces"] == [1]

    def test_does_not_mutate_the_delta_list(self):
        delta_list = [{"x": 1}]
        state = {"judgments": []}
        _merge_state_update(state, {"judgments": delta_list})
        state["judgments"].append({"y": 2})
        assert len(delta_list) == 1


class TestTally:
    def test_counts_passed_and_failed(self):
        assert _tally([{"passed": True}, {"passed": False}, {"passed": True}]) == (2, 1)

    def test_empty(self):
        assert _tally([]) == (0, 0)


@pytest.mark.asyncio
async def test_worker_accumulation_matches_graph_state():
    """
    End-to-end guard: the totals the worker would publish must equal the totals
    LangGraph itself accumulates via its reducers.
    """
    gen = ScenarioGenerationResponse(
        scenarios=[
            ScenarioGenerationItem(
                title=f"S{i}", description=f"D{i}", expected_outcome=f"O{i}"
            )
            for i in range(3)
        ]
    )
    judged = JudgmentResponse(score=40.0, reasoning="weak", passed=False)
    evo = EvolutionSuggestionResponse(
        failure_pattern_synthesis="pattern",
        suggested_system_prompt="better prompt",
    )

    initial = {
        "run_id": "00000000-0000-0000-0000-000000000001",
        "tenant_id": "00000000-0000-0000-0000-000000000002",
        "agent_id": "00000000-0000-0000-0000-000000000003",
        "config": {
            "max_budget_usd": 100.0,
            "target_endpoint_url": "http://dummy",
            "connector_type": "rest_api",
            "scenario_count": 3,
        },
        "total_cost_usd": 0.0,
        "turn_count": 0,
        "scenarios": [],
        "traces": [],
        "judgments": [],
        "evolution_suggestions": [],
        "drift_profile": None,
        "errors": [],
    }

    with patch("backend.agents.generator.llm_client.generate", new_callable=AsyncMock,
               return_value={"parsed": gen, "cost_usd": 0.01}), \
         patch("backend.agents.simulator.upload_trace_with_retry", new_callable=AsyncMock,
               return_value="tenants/t/runs/r/trace.json"), \
         patch("backend.agents.simulator.TargetAgentConnector") as conn_cls, \
         patch("backend.agents.judge.s3_client.download_trace", new_callable=AsyncMock,
               return_value=b'{"interactions": []}'), \
         patch("backend.agents.judge.llm_client.generate", new_callable=AsyncMock,
               return_value={"parsed": judged, "cost_usd": 0.02}), \
         patch("backend.agents.analyzer.vector_db.init_collection", new_callable=AsyncMock), \
         patch("backend.agents.analyzer.s3_client.download_trace", new_callable=AsyncMock,
               return_value=b'{"interactions": []}'), \
         patch("backend.agents.analyzer.llm_client.generate", new_callable=AsyncMock,
               return_value={"content": "summary", "cost_usd": 0.005}), \
         patch("backend.agents.analyzer.llm_client.embed", new_callable=AsyncMock,
               return_value={"vector": [0.1] * 768, "cost_usd": 0.001}), \
         patch("backend.agents.analyzer.vector_db.upsert_trace_embedding",
               new_callable=AsyncMock), \
         patch("backend.agents.analyzer._resolve_baseline", new_callable=AsyncMock,
               return_value=None), \
         patch("backend.agents.evolution.llm_client.generate", new_callable=AsyncMock,
               return_value={"parsed": evo, "cost_usd": 0.03}):

        connector = AsyncMock()
        connector.send_interaction.return_value = {"status": "complete"}
        conn_cls.return_value = connector

        graph = build_evaluation_graph()

        authoritative = await graph.ainvoke(dict(initial))

        rebuilt = dict(initial)
        async for event in graph.astream(dict(initial), stream_mode="updates"):
            for _node, delta in event.items():
                _merge_state_update(rebuilt, delta)

    assert rebuilt["total_cost_usd"] == pytest.approx(
        authoritative["total_cost_usd"]
    ), "worker-reported cost diverges from the graph's own accumulation"
    assert rebuilt["turn_count"] == authoritative["turn_count"]
    # Sanity: the run really did cost more than any single node's delta.
    assert authoritative["total_cost_usd"] > 0.03
    assert len(rebuilt["judgments"]) == len(authoritative["judgments"]) == 3


@pytest.mark.asyncio
async def test_full_run_config_reaches_the_generator():
    """
    scenario_count was validated, stored, then dropped: the worker only forwarded
    five keys, so the generator always fell back to its own default.
    """
    from backend.db.models.agent_config import ConnectorType
    from backend.worker.tasks import _build_graph_config

    class FakeAgent:
        id = "agent-1"
        endpoint_url = "https://agent.example.com/chat"
        connector_type = ConnectorType.REST_API
        description = "desc"
        system_prompt = "prompt"
        tool_definitions = [{"name": "t"}]
        auth_config_encrypted = None

    class FakeRun:
        agent_config = FakeAgent()
        run_config = {
            "scenario_count": 25,
            "eval_budget_usd": 3.0,
            "categories": ["safety"],
            "severity_levels": ["high"],
            "pass_threshold": 65.0,
            "judge_model": None,
        }

    config = _build_graph_config(FakeRun())

    assert config["scenario_count"] == 25
    assert config["max_budget_usd"] == 3.0
    assert config["categories"] == ["safety"]
    assert config["severity_levels"] == ["high"]
    assert config["pass_threshold"] == 65.0
    assert config["target_tools"] == [{"name": "t"}]
    assert config["judge_model"]


@pytest.mark.asyncio
async def test_generator_honours_requested_scenario_count():
    from backend.agents.generator import generate_scenarios_node

    captured = {}

    async def fake_generate(*args, **kwargs):
        captured["messages"] = kwargs["messages"]
        return {
            "parsed": ScenarioGenerationResponse(
                scenarios=[
                    ScenarioGenerationItem(
                        title=f"S{i}", description="d", expected_outcome="o"
                    )
                    for i in range(25)
                ]
            ),
            "cost_usd": 0.01,
        }

    with patch("backend.agents.generator.llm_client.generate", side_effect=fake_generate):
        result = await generate_scenarios_node(
            {"config": {"scenario_count": 25}, "total_cost_usd": 0.0, "turn_count": 0}
        )

    assert "exactly 25" in captured["messages"][0]["content"]
    assert len(result["scenarios"]) == 25
    # Fields persistence actually reads must be present.
    first = result["scenarios"][0]
    assert first["expected_behavior"] == "o"
    assert first["category"]
    assert first["severity"]
    assert first["input_payload"]["messages"]


def test_asyncio_is_available():
    """Guards against the import being removed by a lint autofix."""
    assert asyncio is not None
