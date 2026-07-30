import logging
from typing import Any, Dict

from backend.agents.state import EvaluationState
from backend.connectors.llm import LLMClient
from backend.core.config import get_settings
from backend.schemas.evaluation import EvolutionSuggestionResponse

logger = logging.getLogger(__name__)
settings = get_settings()

llm_client = LLMClient()

# Judgments at or below this score are treated as worth learning from, even if
# the judge marked them as passing.
LOW_SCORE_THRESHOLD = 80.0
MAX_FAILURES_IN_PROMPT = 5

# Re-exported for backwards compatibility with existing imports/tests.
EvolutionSuggestion = EvolutionSuggestionResponse


def _score_of(judgment: Dict[str, Any]) -> float:
    """
    Read a judgment's score.

    Accepts both `overall_score` (current) and `score` (older shape) so a mixed
    state from an in-flight run cannot silently fall back to a default of 100
    and disable the low-score filter entirely — which is exactly what used to
    happen when the judge emitted `score` and this node read `overall_score`.
    """
    for key in ("overall_score", "score"):
        value = judgment.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return 100.0


async def evolution_node(state: EvaluationState) -> Dict[str, Any]:
    """
    Analyzes failed judgments and proposes concrete fixes to the agent's system prompt and tools.
    """
    judgments = state.get("judgments", [])
    config = state.get("config", {})

    # Filter for failed or low-score judgments
    failed_judgments = [
        j for j in judgments
        if _score_of(j) < LOW_SCORE_THRESHOLD or not j.get("passed", True)
    ]

    if not failed_judgments:
        # Agent is performing perfectly
        return {"evolution_suggestions": []}

    current_prompt = config.get("target_system_prompt", "")
    current_tools = config.get("target_tools", [])

    # We only take the worst failures to fit the context window.
    failed_judgments = sorted(failed_judgments, key=_score_of)[:MAX_FAILURES_IN_PROMPT]

    failures_text = "\n\n".join([
        f"Trace ID: {j.get('trace_id')}\n"
        f"Score: {_score_of(j)}\n"
        f"Safety: {j.get('safety_score')} | Correctness: {j.get('correctness_score')} | "
        f"Instruction-following: {j.get('instruction_following_score')}\n"
        f"Reasoning: {j.get('reasoning')}"
        for j in failed_judgments
    ])

    prompt = (
        "You are an expert AI Engineer. The following agent has failed on several evaluation scenarios.\n\n"
        f"CURRENT SYSTEM PROMPT:\n{current_prompt}\n\n"
        f"CURRENT TOOLS:\n{current_tools}\n\n"
        f"FAILURES:\n{failures_text}\n\n"
        "Analyze the failures, identify the root cause pattern, and propose a concrete rewrite of the "
        "system prompt that would fix these issues. Make the prompt more robust without losing its original intent."
    )

    try:
        res = await llm_client.generate(
            model=config.get("judge_model") or settings.DEFAULT_JUDGE_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_model=EvolutionSuggestionResponse,
            max_tokens=2000,
        )

        suggestion: EvolutionSuggestionResponse = res["parsed"]

        suggestion_dict = {
            "failure_pattern": suggestion.failure_pattern_synthesis,
            "suggested_prompt": suggestion.suggested_system_prompt,
            "suggested_tools": suggestion.suggested_tool_changes,
            "based_on_trace_ids": [j.get("trace_id") for j in failed_judgments],
            "status": "pending_review",
        }

        return {
            "evolution_suggestions": [suggestion_dict],
            "total_cost_usd": res["cost_usd"],
        }

    except Exception as e:
        logger.error(f"Evolution Node failed: {e}")
        return {"errors": [f"Evolution node error: {str(e)}"]}
