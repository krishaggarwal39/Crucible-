import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from backend.agents.state import EvaluationState
from backend.connectors.llm import LLMClient
from backend.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

llm_client = LLMClient()

class EvolutionSuggestion(BaseModel):
    failure_pattern_synthesis: str = Field(description="Analysis of the common thread between failures")
    suggested_system_prompt: str = Field(description="A concrete rewrite of the system prompt that addresses the failures")
    suggested_tool_changes: Optional[str] = Field(description="Any recommendations to modify tool schemas, if applicable")

async def evolution_node(state: EvaluationState) -> Dict[str, Any]:
    """
    Analyzes failed judgments and proposes concrete fixes to the agent's system prompt and tools.
    """
    judgments = state.get("judgments", [])
    config = state.get("config", {})
    
    # Filter for failed or low-score judgments
    failed_judgments = [
        j for j in judgments 
        if j.get("overall_score", 100) < 80 or not j.get("passed", True)
    ]
    
    if not failed_judgments:
        # Agent is performing perfectly
        return {"evolution_suggestions": []}
        
    current_prompt = config.get("target_system_prompt", "")
    current_tools = config.get("target_tools", [])
    
    # We only take the top 5 failures to fit in context window and focus on worst issues
    failed_judgments = sorted(failed_judgments, key=lambda x: x.get("overall_score", 100))[:5]
    
    failures_text = "\n\n".join([
        f"Trace ID: {j.get('trace_id')}\nScore: {j.get('overall_score')}\nReasoning: {j.get('reasoning')}"
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
            model=settings.DEFAULT_JUDGE_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_model=EvolutionSuggestion,
            max_tokens=2000
        )
        
        suggestion: EvolutionSuggestion = res["parsed"]
        
        suggestion_dict = {
            "failure_pattern": suggestion.failure_pattern_synthesis,
            "suggested_prompt": suggestion.suggested_system_prompt,
            "suggested_tools": suggestion.suggested_tool_changes,
            "status": "pending_review"
        }
        
        return {
            "evolution_suggestions": [suggestion_dict],
            "total_cost_usd": res["cost_usd"]
        }
        
    except Exception as e:
        logger.error(f"Evolution Node failed: {e}")
        return {"errors": [f"Evolution node error: {str(e)}"]}
