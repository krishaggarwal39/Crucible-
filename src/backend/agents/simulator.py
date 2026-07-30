import asyncio
import json
import logging
import time
import uuid
from typing import Any, Dict

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from backend.agents.state import EvaluationState
from backend.connectors.s3 import S3BlobStore
from backend.connectors.target_agent import TargetAgentConnector
from backend.core.config import get_settings
from backend.db.models.agent_config import ConnectorType

logger = logging.getLogger(__name__)
settings = get_settings()

s3_client = S3BlobStore()

DEFAULT_MAX_TURNS = 5
# Rough token estimate for dashboard filtering; not billing-grade.
CHARS_PER_TOKEN = 4


class SimulatorError(Exception):
    pass


@retry(
    retry=retry_if_exception_type(Exception),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    stop=stop_after_attempt(3),
    reraise=True,
)
async def upload_trace_with_retry(tenant_id: str, run_id: str, trace_id: str, payload: bytes) -> str:
    """Robust S3 upload with Tenacity retries to prevent simulation crashes on transient network issues."""
    return await s3_client.upload_trace(tenant_id, run_id, trace_id, payload)


def _count_tool_calls(interaction_log: list[dict]) -> int:
    """
    Count tool invocations visible in the transcript.

    Target agents report these in different shapes, so check the common ones
    rather than assuming a single schema.
    """
    count = 0
    for entry in interaction_log:
        content = entry.get("content")
        if not isinstance(content, dict):
            continue
        for key in ("tool_calls", "toolCalls", "tools_used"):
            value = content.get(key)
            if isinstance(value, list):
                count += len(value)
        if content.get("tool_name") or content.get("tool"):
            count += 1
    return count


async def simulate_scenario(
    scenario: Dict[str, Any],
    connector: Any,
    tenant_id: str,
    run_id: str,
    max_turns: int = DEFAULT_MAX_TURNS,
) -> Dict[str, Any]:
    """
    Simulates a single scenario against the target agent in a multi-turn environment loop.
    Uploads the raw trace to S3 and returns only the metadata for the graph state.
    """
    interaction_log: list[dict] = []
    started = time.monotonic()
    failed = False
    failure_reason: str | None = None

    # 1. Red Team generates initial input
    red_team_input = (
        f"Execute scenario: {scenario['title']}. Instructions: {scenario['description']}"
    )
    interaction_log.append({"role": "red_team", "content": red_team_input})

    current_input = red_team_input

    payload = scenario.get("input_payload") or {}
    # Copy so the shared scenario dict in graph state is never mutated.
    payload = dict(payload) if isinstance(payload, dict) else {}
    payload["message"] = current_input

    for turn in range(max_turns):
        try:
            target_response = await connector.send_interaction(payload)
            interaction_log.append({"role": "target_agent", "content": target_response})

            # More robust termination logic: if agent explicitly returns a termination
            # status, or if we're just doing a basic 1-turn request-response ping
            is_complete = False
            if isinstance(target_response, dict):
                status_val = str(target_response.get("status", "")).lower()
                is_complete = status_val in ["complete", "done", "success"]

            if is_complete or turn == max_turns - 1:
                break

            # Simulate Environment / Tool Execution (still a stub — the environment
            # side is not yet real, so scenarios cannot fail on tool results).
            current_input = "Environment feedback: Tool executed successfully."
            payload["message"] = current_input
            interaction_log.append({"role": "environment", "content": current_input})

        except Exception as e:
            logger.error(
                f"Simulator error for scenario {scenario['id']} on turn {turn}: {e}"
            )
            interaction_log.append({"role": "error", "content": str(e)})
            failed = True
            failure_reason = str(e)
            break

    duration_ms = int((time.monotonic() - started) * 1000)

    # Save trace to S3
    trace_id = str(uuid.uuid4())
    trace_body = json.dumps({
        "scenario_id": scenario["id"],
        "interactions": interaction_log,
    })
    trace_payload = trace_body.encode("utf-8")

    # Metrics the TraceMetadata model documents as "populated after simulation".
    # These were previously hardcoded to 0 on every row.
    metrics = {
        "turn_count": len(interaction_log),
        "duration_ms": duration_ms,
        "token_count": max(1, len(trace_body) // CHARS_PER_TOKEN),
        "tool_call_count": _count_tool_calls(interaction_log),
    }

    try:
        s3_key = await upload_trace_with_retry(
            tenant_id=tenant_id,
            run_id=run_id,
            trace_id=trace_id,
            payload=trace_payload,
        )
    except Exception as e:
        logger.error(f"Failed to upload trace {trace_id} after retries: {e}")
        return {
            "trace_id": trace_id,
            "scenario_id": scenario["id"],
            "storage_key": None,
            "status": "failed",
            "error_message": f"Trace upload failed: {str(e)}",
            **metrics,
        }

    return {
        "trace_id": trace_id,
        "scenario_id": scenario["id"],
        "storage_key": s3_key,
        "status": "failed" if failed else "completed",
        "error_message": failure_reason,
        **metrics,
    }


async def simulate_environment_node(state: EvaluationState) -> Dict[str, Any]:
    """
    Executes all generated scenarios against the target agent concurrently.
    Uploads raw traces to S3 and returns trace metadata for the state.
    """
    scenarios = state.get("scenarios", [])
    config = state.get("config", {})
    tenant_id = state.get("tenant_id")
    run_id = state.get("run_id")

    # State Validation
    if not tenant_id or not run_id:
        return {
            "errors": [
                "Missing critical state variables (tenant_id, run_id) in simulator node"
            ]
        }

    if not scenarios:
        return {}

    connector_type_str = config.get("connector_type")

    try:
        connector_type = (
            ConnectorType(connector_type_str) if connector_type_str else ConnectorType.REST_API
        )
    except ValueError:
        return {"errors": [f"Invalid connector type: {connector_type_str}"]}

    target_url = config.get("target_endpoint_url")
    if not target_url and connector_type == ConnectorType.REST_API:
        return {
            "errors": [
                "Target agent endpoint URL is missing in config for REST_API connector"
            ]
        }

    try:
        if connector_type == ConnectorType.REST_API:
            # Pass the agent's stored credential. Previously the connector was
            # built without it, so every request to every target agent went out
            # unauthenticated and any agent behind auth returned 401 — which the
            # judge then scored as an agent failure.
            connector = TargetAgentConnector(
                target_url,
                bearer_token=config.get("target_bearer_token"),
            )
        else:
            raise ValueError(f"Unsupported connector type: {connector_type}")
    except Exception as e:
        logger.error(f"Simulator failed to init connector: {e}")
        return {"errors": [f"Failed to initialize target agent connector: {str(e)}"]}

    max_turns = int(config.get("max_turns") or DEFAULT_MAX_TURNS)
    semaphore = asyncio.Semaphore(max(1, settings.NODE_CONCURRENCY_LIMIT))

    async def _simulate_with_semaphore(scenario):
        async with semaphore:
            return await simulate_scenario(
                scenario, connector, tenant_id, run_id, max_turns=max_turns
            )

    try:
        results = await asyncio.gather(
            *[_simulate_with_semaphore(s) for s in scenarios],
            return_exceptions=True,
        )
    finally:
        # Always release the HTTP client and Redis circuit-breaker connection.
        if hasattr(connector, "close"):
            await connector.close()

    traces = []
    errors = []

    for res in results:
        if isinstance(res, BaseException):
            errors.append(str(res))
        elif isinstance(res, dict) and res.get("trace_id"):
            traces.append(res)
            # A trace can be kept (so the judge can see the failure) while still
            # surfacing its error. The old code checked for an "error" key that
            # simulate_scenario never produced, so these were silently dropped.
            if res.get("error_message"):
                errors.append(
                    f"Scenario {res.get('scenario_id')}: {res['error_message']}"
                )
        else:
            errors.append(f"Unknown result format: {res}")

    state_update = {
        "traces": traces,
        # Target-agent calls carry no LLM cost of their own.
        "total_cost_usd": 0.0,
        "turn_count": len(scenarios),
    }

    if errors:
        state_update["errors"] = errors

    return state_update
