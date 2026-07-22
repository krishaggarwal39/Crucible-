import asyncio
import json
import logging
from typing import Any, Dict

from backend.agents.state import EvaluationState
from backend.agents.prompts import JUDGE_PROMPT
from backend.connectors.llm import LLMClient
from backend.connectors.s3 import S3BlobStore
from backend.schemas.evaluation import JudgmentResponse
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

MAX_TRACE_PAYLOAD_SIZE = 100000

llm_client = LLMClient()
s3_client = S3BlobStore()

async def _download_trace_with_retry(storage_key: str) -> bytes:
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def _download():
        return await s3_client.download_trace(storage_key)
    return await _download()

async def judge_single_trace(trace_meta: Dict[str, Any], state: EvaluationState, semaphore: asyncio.Semaphore) -> Dict[str, Any]:
    async with semaphore:
        scenario_id = trace_meta.get("scenario_id")
        storage_key = trace_meta.get("storage_key")
    
    scenario = next((s for s in state.get("scenarios", []) if s["id"] == scenario_id), None)
    
    if not scenario:
        return {"error": f"Scenario {scenario_id} not found for trace {storage_key}"}
        
    if not storage_key:
        return {
            "judgment": {
                "scenario_id": scenario_id,
                "trace_key": storage_key,
                "score": 0.0,
                "reasoning": "UNJUDGEABLE - NO TRACE (SIMULATION FAILED)",
                "passed": False
            },
            "cost_usd": 0.0
        }
        
    try:
        # Fetch the raw trace payload from S3 instead of LangGraph state
        trace_payload = await _download_trace_with_retry(storage_key)
        trace_string = trace_payload.decode("utf-8")
        
        if len(trace_string) > MAX_TRACE_PAYLOAD_SIZE:
            return {
                "judgment": {
                    "scenario_id": scenario_id,
                    "trace_key": storage_key,
                    "score": 0.0,
                    "reasoning": "UNJUDGEABLE - PAYLOAD TOO LARGE",
                    "passed": False
                },
                "cost_usd": 0.0
            }
            
        trace_data = json.loads(trace_string)
        interactions = trace_data.get("interactions", [])
        
        prompt = JUDGE_PROMPT.format(
            scenario_title=scenario["title"],
            scenario_description=scenario["description"],
            trace=json.dumps(interactions, indent=2)
        )
        
        messages = [{"role": "user", "content": prompt}]
        
        res = await llm_client.generate(
            model="gpt-4o",
            messages=messages,
            response_model=JudgmentResponse
        )
        
        parsed: JudgmentResponse = res["parsed"]
        
        return {
            "judgment": {
                "scenario_id": scenario_id,
                "trace_key": storage_key,
                "score": parsed.score,
                "reasoning": parsed.reasoning,
                "passed": parsed.passed
            },
            "cost_usd": res["cost_usd"]
        }
    except Exception as e:
        logger.error(f"Judge failed for scenario {scenario_id}: {e}")
        return {
            "judgment": {
                "scenario_id": scenario_id,
                "trace_key": storage_key,
                "score": 0.0,
                "reasoning": f"LLM Judge Error: {str(e)}",
                "passed": False
            },
            "cost_usd": 0.0
        }


async def judge_trace_node(state: EvaluationState) -> Dict[str, Any]:
    """
    Judges all unjudged traces concurrently.
    """
    if not state.get("traces"):
        return {}
        
    judged_scenario_ids = {j.get("scenario_id") for j in state.get("judgments", [])}
    unjudged_traces = [t for t in state["traces"] if t.get("scenario_id") not in judged_scenario_ids]
    
    if not unjudged_traces:
        return {}
        
    total_cost = 0.0
    errors = []
    new_judgments = []
    
    # Process judgments concurrently with a semaphore to prevent memory/rate limit exhaustion
    semaphore = asyncio.Semaphore(10)
    tasks = [judge_single_trace(trace, state, semaphore) for trace in unjudged_traces]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    for res in results:
        if isinstance(res, Exception):
            errors.append(str(res))
        elif "error" in res:
            errors.append(res["error"])
        else:
            new_judgments.append(res["judgment"])
            total_cost += res["cost_usd"]
            
    state_update = {
        "judgments": new_judgments,
        "total_cost_usd": total_cost,
        "turn_count": len(unjudged_traces),
    }
    
    if errors:
        state_update["errors"] = errors
        
    return state_update
