import asyncio
import logging
import uuid
import json
from typing import Any, Dict, List

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from backend.agents.state import EvaluationState
from backend.connectors.target_agent import TargetAgentConnector
from backend.connectors.s3 import S3BlobStore
from backend.core.config import get_settings
from backend.db.models.agent_config import ConnectorType

logger = logging.getLogger(__name__)
settings = get_settings()

s3_client = S3BlobStore()

class SimulatorError(Exception):
    pass

@retry(
    retry=retry_if_exception_type(Exception),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    stop=stop_after_attempt(3),
    reraise=True
)
async def upload_trace_with_retry(tenant_id: str, run_id: str, trace_id: str, payload: bytes) -> str:
    """Robust S3 upload with Tenacity retries to prevent simulation crashes on transient network issues."""
    return await s3_client.upload_trace(tenant_id, run_id, trace_id, payload)


async def simulate_scenario(scenario: Dict[str, Any], connector: Any, tenant_id: str, run_id: str) -> Dict[str, Any]:
    """
    Simulates a single scenario against the target agent in a multi-turn environment loop.
    Uploads the raw trace to S3 and returns only the metadata for the graph state.
    """
    interaction_log = []
    
    # 1. Red Team generates initial input
    red_team_input = f"Execute scenario: {scenario['title']}. Instructions: {scenario['description']}"
    interaction_log.append({"role": "red_team", "content": red_team_input})
    
    # Environment Loop (Max 5 turns for now)
    max_turns = 5
    current_input = red_team_input
    
    for turn in range(max_turns):
        try:
            target_response = await connector.send_interaction({"message": current_input})
            interaction_log.append({"role": "target_agent", "content": target_response})
            
            # Simple heuristic for loop termination: if agent says it's done or no tools called
            # In a full system, the Red Team LLM would evaluate if the scenario is complete
            if target_response.get("status") == "complete" or "tool_calls" not in target_response:
                break
                
            # Simulate Environment / Tool Execution (Mocked for now)
            current_input = "Environment feedback: Tool executed successfully."
            interaction_log.append({"role": "environment", "content": current_input})
            
        except Exception as e:
            logger.error(f"Simulator error for scenario {scenario['id']} on turn {turn}: {e}")
            interaction_log.append({"role": "error", "content": str(e)})
            break

    # Save trace to S3
    trace_id = str(uuid.uuid4())
    trace_payload = json.dumps({
        "scenario_id": scenario["id"],
        "interactions": interaction_log
    }).encode("utf-8")
    
    try:
        s3_key = await upload_trace_with_retry(
            tenant_id=tenant_id,
            run_id=run_id,
            trace_id=trace_id,
            payload=trace_payload
        )
    except Exception as e:
        logger.error(f"Failed to upload trace {trace_id} after retries: {e}")
        return {
            "trace_id": trace_id,
            "scenario_id": scenario["id"],
            "storage_key": None,
            "error_message": f"Trace upload failed: {str(e)}"
        }
    
    return {
        "trace_id": trace_id,
        "scenario_id": scenario["id"],
        "storage_key": s3_key,
        "turn_count": len(interaction_log)
    }


async def simulate_environment_node(state: EvaluationState) -> Dict[str, Any]:
    """
    Executes all generated scenarios against the target agent concurrently.
    Uploads raw traces to S3 and returns trace metadata for the state.
    """
    scenarios = state.get("scenarios", [])
    if not scenarios:
        return {}
        
    config = state.get("config", {})
    connector_type_str = config.get("connector_type")
    
    # Map string back to Enum if needed, or handle string directly
    try:
        connector_type = ConnectorType(connector_type_str) if connector_type_str else ConnectorType.REST_API
    except ValueError:
        return {"errors": [f"Invalid connector type: {connector_type_str}"]}

    target_url = config.get("target_endpoint_url")
    if not target_url and connector_type == ConnectorType.REST_API:
        return {"errors": ["Target agent endpoint URL is missing in config for REST_API connector"]}
        
    try:
        if connector_type == ConnectorType.REST_API:
            connector = TargetAgentConnector(target_url)
        elif connector_type == ConnectorType.SDK:
            # Placeholder for SDK connector
            raise NotImplementedError("SDK connector not yet implemented")
        elif connector_type == ConnectorType.MCP:
            # Placeholder for MCP connector
            raise NotImplementedError("MCP connector not yet implemented")
        else:
            raise ValueError(f"Unsupported connector type: {connector_type}")
    except Exception as e:
        logger.error(f"Simulator failed to init connector: {e}")
        return {"errors": [f"Failed to initialize target agent connector: {str(e)}"]}
    
    tenant_id = state.get("tenant_id", "default_tenant")
    run_id = state.get("run_id", "default_run")
    
    total_cost = 0.0
    
    # Process scenarios concurrently using asyncio.gather
    semaphore = asyncio.Semaphore(10)
    
    async def _simulate_with_semaphore(scenario):
        async with semaphore:
            return await simulate_scenario(scenario, connector, tenant_id, run_id)
            
    tasks = [
        _simulate_with_semaphore(scenario)
        for scenario in scenarios
    ]
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    traces = []
    errors = []
    
    for res in results:
        if isinstance(res, Exception):
            errors.append(str(res))
        elif isinstance(res, dict) and "error" in res:
            errors.append(res["error"])
        elif isinstance(res, dict) and res.get("trace_id"):
            traces.append(res)
        else:
            errors.append(f"Unknown result format: {res}")
            
    # Close the connector to release resources (like Redis circuit breaker and HTTP client)
    if hasattr(connector, "close"):
        await connector.close()
        
    state_update = {
        "traces": traces,
        "total_cost_usd": total_cost,
        "turn_count": len(scenarios)
    }
    
    if errors:
        state_update["errors"] = errors
        
    return state_update
