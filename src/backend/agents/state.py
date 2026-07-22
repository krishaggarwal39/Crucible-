import operator
from typing import Annotated, Any, Dict, List, TypedDict


class EvaluationState(TypedDict):
    """
    State definition for the Evaluation LangGraph.
    Uses Annotated with operator.add for lists to guarantee additive, parallel-safe merges.
    """
    run_id: str
    tenant_id: str
    agent_id: str
    config: Dict[str, Any]
    
    # Trackers for infinite loops and cost
    total_cost_usd: Annotated[float, operator.add]
    turn_count: Annotated[int, operator.add]
    
    # Data Pointers (Metadata only, NO huge payloads)
    scenarios: Annotated[List[Dict[str, Any]], operator.add]
    traces: Annotated[List[Dict[str, Any]], operator.add]
    judgments: Annotated[List[Dict[str, Any]], operator.add]
    evolution_suggestions: Annotated[List[Dict[str, Any]], operator.add]
    
    # AI Operations
    drift_profile: Dict[str, Any]
    
    # Error tracking
    errors: Annotated[List[str], operator.add]
