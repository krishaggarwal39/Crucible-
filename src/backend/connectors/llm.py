import logging
from typing import Any, Dict, List

import tiktoken
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

import litellm
from litellm.exceptions import RateLimitError, APIConnectionError

from backend.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class ContextWindowExceededError(Exception):
    pass


class LLMClient:
    def __init__(self):
        self.fallbacks = settings.FALLBACK_MODELS
        # Disable litellm telemetry
        litellm.telemetry = False

    def _estimate_tokens(self, model: str, messages: List[Dict[str, str]]) -> int:
        """Pre-flight check to prevent sending payloads that exceed context window."""
        try:
            # Drop provider prefix for tiktoken (e.g., 'openai/gpt-4o' -> 'gpt-4o')
            model_name = model.split("/")[-1]
            encoding = tiktoken.encoding_for_model(model_name)
        except KeyError:
            encoding = tiktoken.get_encoding("cl100k_base")

        num_tokens = 0
        for message in messages:
            num_tokens += 4
            for _, value in message.items():
                if isinstance(value, str):
                    num_tokens += len(encoding.encode(value))
        num_tokens += 2
        return num_tokens

    def check_context_window(self, model: str, messages: List[Dict[str, str]], max_tokens_out: int) -> None:
        num_tokens = self._estimate_tokens(model, messages)
        model_info = litellm.model_cost.get(model, {})
        # Fallback to 128k if unknown
        max_context = model_info.get("max_tokens", 128000)

        if num_tokens + max_tokens_out > max_context:
            raise ContextWindowExceededError(
                f"Estimated input tokens ({num_tokens}) + requested max_tokens ({max_tokens_out}) "
                f"exceeds model {model} limit ({max_context})"
            )

    @retry(
        retry=retry_if_exception_type((RateLimitError, APIConnectionError)),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(3),
        reraise=True
    )
    async def generate(self, model: str, messages: List[Dict[str, str]], **kwargs) -> Dict[str, Any]:
        """
        Generates a completion with retries, config-driven fallbacks, and exact cost tracking.
        """
        max_tokens = kwargs.get("max_tokens", 1024)
        self.check_context_window(model, messages, max_tokens)

        # Let LiteLLM natively handle the fallback routing
        try:
            response_model = kwargs.pop("response_model", None)
            if response_model:
                import instructor
                client = instructor.from_litellm(litellm.acompletion)
                parsed, response = await client.chat.completions.create_with_completion(
                    model=model,
                    messages=messages,
                    response_model=response_model,
                    fallbacks=self.fallbacks,
                    timeout=60,
                    **kwargs
                )
            else:
                response = await litellm.acompletion(
                    model=model,
                    messages=messages,
                    fallbacks=self.fallbacks,
                    timeout=60,
                    **kwargs
                )
                parsed = None
        except Exception as e:
            logger.error(f"LLM Generation failed: {e}")
            raise

        # Track costs rigorously
        try:
            cost = litellm.completion_cost(completion_response=response)
        except Exception:
            cost = 0.0

        usage = response.usage

        return {
            "content": response.choices[0].message.content if response.choices else "",
            "parsed": parsed,
            "cost_usd": cost or 0.0,
            "prompt_tokens": usage.prompt_tokens if usage else 0,
            "completion_tokens": usage.completion_tokens if usage else 0,
            "total_tokens": usage.total_tokens if usage else 0,
            "model_used": response.model,
        }

    @retry(
        retry=retry_if_exception_type((RateLimitError, APIConnectionError)),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(3),
        reraise=True
    )
    async def embed(self, model: str, input_text: str) -> Dict[str, Any]:
        """
        Generates an embedding for the input text using litellm.aembedding.
        """
        try:
            response = await litellm.aembedding(
                model=model,
                input=input_text,
                timeout=60
            )
            # Track costs
            try:
                # litellm can track embedding costs if price is known
                cost = litellm.completion_cost(completion_response=response)
            except Exception:
                cost = 0.0
                
            return {
                "vector": response.data[0]["embedding"],
                "cost_usd": cost or 0.0
            }
        except Exception as e:
            logger.error(f"LLM Embedding failed: {e}")
            raise
