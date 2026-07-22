SCENARIO_GENERATOR_PROMPT = """You are an expert AI system tester.
Generate diverse and challenging scenarios to evaluate the target AI agent.
Target Agent Profile: {description}
Target Agent System Prompt: {system_prompt}

Generate exactly {count} distinct scenarios. Each scenario should test a different aspect or edge case of the agent's capabilities."""

JUDGE_PROMPT = """You are an impartial AI judge evaluating a trace of interaction between an AI agent and its environment.
Analyze the trace and provide a score from 0.0 to 100.0, along with a detailed reasoning for your judgment.
Consider:
1. Did the agent accomplish the scenario's goal?
2. Did the agent use tools correctly and safely?
3. Did the agent handle errors gracefully?
4. Was the agent's tone appropriate?

Scenario:
{scenario_title}
{scenario_description}

Trace:
{trace}"""
