from typing import Any, Dict
import uuid

from backend.agents.state import EvaluationState
from backend.agents.prompts import SCENARIO_GENERATOR_PROMPT
from backend.connectors.llm import LLMClient
from backend.schemas.evaluation import ScenarioGenerationResponse

llm_client = LLMClient()

async def generate_scenarios_node(state: EvaluationState) -> Dict[str, Any]:
    """
    Generates test scenarios for the target agent using an LLM.
    Enforces structured output using Pydantic and Instructor.
    """
    config = state.get("config", {})
    count = config.get("scenario_count", 3)
    target_desc = config.get("target_description", "An AI agent")
    target_prompt = config.get("target_system_prompt", "You are a helpful assistant")
    
    prompt = SCENARIO_GENERATOR_PROMPT.format(
        count=count,
        description=target_desc,
        system_prompt=target_prompt
    )
    
    messages = [{"role": "user", "content": prompt}]
    
    try:
        res = await llm_client.generate(
            model="gpt-4o",
            messages=messages,
            response_model=ScenarioGenerationResponse
        )
    except Exception as e:
        return {"errors": [f"Scenario generation failed: {str(e)}"]}
        
    parsed: ScenarioGenerationResponse = res["parsed"]
    cost = res["cost_usd"]
    
    scenarios = []
    for s in parsed.scenarios:
        scenarios.append({
            "id": str(uuid.uuid4()),
            "title": s.title,
            "description": s.description,
            "expected_outcome": s.expected_outcome
        })
        
    return {
        "scenarios": scenarios,
        "total_cost_usd": cost,
        "turn_count": 1
    }
