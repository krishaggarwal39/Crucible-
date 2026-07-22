from typing import Any, Dict, Literal

from langgraph.graph import END, StateGraph

from backend.agents.state import EvaluationState

from backend.agents.generator import generate_scenarios_node
from backend.agents.simulator import simulate_environment_node
from backend.agents.judge import judge_trace_node
from backend.agents.analyzer import analyze_drift_node
from backend.agents.evolution import evolution_node


# ── Routing Logic ─────────────────────────────────────────────────────────────

def _is_budget_exceeded(state: EvaluationState) -> bool:
    budget = state.get("config", {}).get("max_budget_usd", 5.0)
    return state.get("total_cost_usd", 0.0) > budget

def route_after_generation(state: EvaluationState) -> Literal["simulate", END]:  # type: ignore
    if _is_budget_exceeded(state) or not state.get("scenarios"):
        return END
    return "simulate"

def route_after_simulation(state: EvaluationState) -> Literal["judge", END]:  # type: ignore
    if _is_budget_exceeded(state) or not state.get("traces"):
        return END
    return "judge"

def route_after_judgment(state: EvaluationState) -> Literal["analyze", END]:  # type: ignore
    if _is_budget_exceeded(state):
        return END
    return "analyze"

def route_after_analyze(state: EvaluationState) -> Literal["evolution", END]:  # type: ignore
    if _is_budget_exceeded(state):
        return END
    return "evolution"

# ── Graph Builder ─────────────────────────────────────────────────────────────

def build_evaluation_graph():
    builder = StateGraph(EvaluationState)
    
    # Add Nodes
    builder.add_node("generate", generate_scenarios_node)
    builder.add_node("simulate", simulate_environment_node)
    builder.add_node("judge", judge_trace_node)
    builder.add_node("analyze", analyze_drift_node)
    builder.add_node("evolution", evolution_node)
    
    # Edges
    builder.set_entry_point("generate")
    
    builder.add_conditional_edges(
        "generate", 
        route_after_generation,
        {"simulate": "simulate", END: END}
    )
    
    builder.add_conditional_edges(
        "simulate",
        route_after_simulation,
        {"judge": "judge", END: END}
    )
    
    builder.add_conditional_edges(
        "judge",
        route_after_judgment,
        {"analyze": "analyze", END: END}
    )
    
    builder.add_conditional_edges(
        "analyze",
        route_after_analyze,
        {"evolution": "evolution", END: END}
    )
    
    builder.add_edge("evolution", END)
    
    return builder.compile()
