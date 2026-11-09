import asyncio
import json
import logging
from typing import Any, Dict

from tenacity import retry, stop_after_attempt, wait_exponential

from backend.agents.prompts import JUDGE_PROMPT
from backend.agents.state import EvaluationState
from backend.connectors.llm import LLMClient
from backend.connectors.s3 import S3BlobStore
from backend.core.config import get_settings
from backend.schemas.evaluation import JudgmentResponse

logger = logging.getLogger(__name__)
settings = get_settings()

MAX_TRACE_PAYLOAD_SIZE = 100000
DEFAULT_PASS_THRESHOLD = 70.0

llm_client = LLMClient()
s3_client = S3BlobStore()


# Defined once at import time rather than rebuilt on every call.
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def _download_trace_with_retry(storage_key: str) -> bytes:
    return await s3_client.download_trace(storage_key)


def _unjudgeable(
    trace_meta: Dict[str, Any], reason: str, judge_model: str
) -> Dict[str, Any]:
    """Build a zero-score judgment for a trace that cannot be evaluated."""
    return {
        "judgment": {
            "scenario_id": trace_meta.get("scenario_id"),
            "trace_id": trace_meta.get("trace_id"),
            "trace_key": trace_meta.get("storage_key"),
            "overall_score": 0.0,
            "safety_score": None,
            "correctness_score": None,
            "instruction_following_score": None,
            "reasoning": reason,
            "passed": False,
            "judge_model": judge_model,
            "raw_output": {"unjudgeable": True, "reason": reason},
        },
        "cost_usd": 0.0,
    }


async def judge_single_trace(
    trace_meta: Dict[str, Any],
    state: EvaluationState,
    semaphore: asyncio.Semaphore,
) -> Dict[str, Any]:
    """
    Judge one trace.

    The whole body is inside the semaphore. Previously only the two dict lookups
    were guarded, so the S3 download and the LLM call — the parts that actually
    need limiting — ran completely unbounded.
    """
    async with semaphore:
        config = state.get("config", {})
        judge_model = config.get("judge_model") or settings.DEFAULT_JUDGE_MODEL
        pass_threshold = config.get("pass_threshold", DEFAULT_PASS_THRESHOLD)

        scenario_id = trace_meta.get("scenario_id")
        storage_key = trace_meta.get("storage_key")

        scenario = next(
            (s for s in state.get("scenarios", []) if s["id"] == scenario_id), None
        )

        if not scenario:
            return {
                "error": f"Scenario {scenario_id} not found for trace {storage_key}"
            }

        if not storage_key:
            return _unjudgeable(
                trace_meta, "UNJUDGEABLE - NO TRACE (SIMULATION FAILED)", judge_model
            )

        try:
            # Fetch the raw trace payload from S3 instead of LangGraph state
            trace_payload = await _download_trace_with_retry(storage_key)
            trace_string = trace_payload.decode("utf-8")

            if len(trace_string) > MAX_TRACE_PAYLOAD_SIZE:
                return _unjudgeable(
                    trace_meta, "UNJUDGEABLE - PAYLOAD TOO LARGE", judge_model
                )

            trace_data = json.loads(trace_string)
            interactions = trace_data.get("interactions", [])

            prompt = JUDGE_PROMPT.format(
                scenario_title=scenario["title"],
                scenario_description=scenario["description"],
                expected_behavior=scenario.get("expected_behavior")
                or scenario.get("expected_outcome", "(not specified)"),
                trace=json.dumps(interactions, indent=2),
                pass_threshold=pass_threshold,
            )

            messages = [{"role": "user", "content": prompt}]

            res = await llm_client.generate(
                model=judge_model,
                messages=messages,
                response_model=JudgmentResponse,
                max_tokens=settings.MAX_TOKENS_JUDGE,
            )

            parsed: JudgmentResponse = res["parsed"]

            return {
                "judgment": {
                    "scenario_id": scenario_id,
                    "trace_id": trace_meta.get("trace_id"),
                    "trace_key": storage_key,
                    # Named to match the Judgment.overall_score column and what
                    # the evolution node reads. It used to be emitted as "score",
                    # so evolution's score filter silently never matched.
                    "overall_score": parsed.score,
                    "safety_score": parsed.safety_score,
                    "correctness_score": parsed.correctness_score,
                    "instruction_following_score": parsed.instruction_following_score,
                    "reasoning": parsed.reasoning,
                    "passed": parsed.passed,
                    "judge_model": judge_model,
                    # Persisted for auditability; the column is NOT NULL and was
                    # previously always {}.
                    "raw_output": parsed.model_dump(),
                },
                "cost_usd": res["cost_usd"],
            }
        except Exception as e:
            logger.error(f"Judge failed for scenario {scenario_id}: {e}")
            return _unjudgeable(trace_meta, f"LLM Judge Error: {str(e)}", judge_model)


async def judge_trace_node(state: EvaluationState) -> Dict[str, Any]:
    """
    Judge all unjudged traces, in budget-aware batches.

    Batching matters: the budget is only re-checked on graph edges, so a single
    unbounded fan-out could spend far past the limit before the next check. We
    process a bounded batch, then stop starting new work once the run's remaining
    budget is exhausted.
    """
    if not state.get("traces"):
        return {}

    judged_scenario_ids = {j.get("scenario_id") for j in state.get("judgments", [])}
    unjudged_traces = [
        t for t in state["traces"] if t.get("scenario_id") not in judged_scenario_ids
    ]

    if not unjudged_traces:
        return {}

    config = state.get("config", {})
    judge_model = config.get("judge_model") or settings.DEFAULT_JUDGE_MODEL
    budget = config.get("max_budget_usd")
    spent_before = state.get("total_cost_usd", 0.0)

    total_cost = 0.0
    errors: list[str] = []
    new_judgments: list[Dict[str, Any]] = []

    batch_size = max(1, settings.NODE_CONCURRENCY_LIMIT)
    semaphore = asyncio.Semaphore(batch_size)

    for start in range(0, len(unjudged_traces), batch_size):
        if budget is not None and (spent_before + total_cost) >= budget:
            remaining = unjudged_traces[start:]
            logger.warning(
                "Budget exhausted after %d/%d traces; skipping %d",
                start, len(unjudged_traces), len(remaining),
            )
            errors.append(
                f"Budget limit reached: {len(remaining)} traces were not judged."
            )
            for trace in remaining:
                new_judgments.append(
                    _unjudgeable(
                        trace, "UNJUDGED - EVALUATION BUDGET EXHAUSTED", judge_model
                    )["judgment"]
                )
            break

        batch = unjudged_traces[start:start + batch_size]
        tasks = [judge_single_trace(trace, state, semaphore) for trace in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for res in results:
            if isinstance(res, BaseException):
                errors.append(str(res))
            elif "error" in res:
                errors.append(res["error"])
            else:
                new_judgments.append(res["judgment"])
                total_cost += res["cost_usd"]

    state_update: Dict[str, Any] = {
        "judgments": new_judgments,
        "total_cost_usd": total_cost,
        "turn_count": len(unjudged_traces),
    }

    if errors:
        state_update["errors"] = errors

    return state_update
