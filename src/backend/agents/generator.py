import uuid
from typing import Any, Dict

from backend.agents.prompts import SCENARIO_GENERATOR_PROMPT
from backend.agents.state import EvaluationState
from backend.connectors.llm import LLMClient
from backend.core.config import get_settings
from backend.schemas.evaluation import ScenarioGenerationResponse

llm_client = LLMClient()
settings = get_settings()

# Used only if the caller supplied no scenario_count at all. The API's RunConfig
# always provides one, so this is a floor for direct/manual graph invocations.
DEFAULT_SCENARIO_COUNT = 10


async def generate_scenarios_node(state: EvaluationState) -> Dict[str, Any]:
    """
    Generates test scenarios for the target agent using an LLM.
    Enforces structured output using Pydantic and Instructor.
    """
    config = state.get("config", {})
    count = config.get("scenario_count") or DEFAULT_SCENARIO_COUNT
    target_desc = config.get("target_description", "An AI agent")
    target_prompt = config.get("target_system_prompt", "You are a helpful assistant")

    # Honour the caller's category/severity filters if they supplied any. These
    # were previously validated and stored but never reached the generator.
    categories = config.get("categories") or []
    severities = config.get("severity_levels") or []
    category_hint = (
        f"\n\nRestrict category to only these values: {', '.join(categories)}."
        if categories else ""
    )
    severity_hint = (
        f"\n\nRestrict severity to only these values: {', '.join(severities)}."
        if severities else ""
    )

    prompt = SCENARIO_GENERATOR_PROMPT.format(
        count=count,
        description=target_desc,
        system_prompt=target_prompt,
        category_hint=category_hint,
        severity_hint=severity_hint,
    )

    messages = [{"role": "user", "content": prompt}]

    try:
        res = await llm_client.generate(
            model=config.get("generator_model") or settings.DEFAULT_GENERATOR_MODEL,
            messages=messages,
            response_model=ScenarioGenerationResponse,
            max_tokens=settings.MAX_TOKENS_GENERATOR,
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
            # `expected_behavior` matches the Scenario column name. The old code
            # emitted only `expected_outcome`, which persistence never read, so
            # every stored scenario had an empty expected_behavior.
            "expected_behavior": s.expected_outcome,
            "expected_outcome": s.expected_outcome,
            "category": s.category,
            "severity": s.severity,
            # Mirrors the format sent to the target agent, as documented on the
            # Scenario model. Previously always persisted as {}.
            "input_payload": {
                "messages": [{"role": "user", "content": s.description}],
            },
            "tags": [s.category],
        })

    return {
        "scenarios": scenarios,
        "total_cost_usd": cost,
        "turn_count": 1,
    }
