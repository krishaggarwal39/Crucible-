import litellm
import pytest
from unittest.mock import patch


from backend.connectors.llm import LLMClient, ContextWindowExceededError

MESSAGES = [{"role": "user", "content": "Hello"}]


def test_context_window_exceeded():
    client = LLMClient()

    # gpt-4o has a 128k context window; ask for more output than that.
    with pytest.raises(ContextWindowExceededError, match="context window"):
        client.check_context_window("gpt-4o", MESSAGES, max_tokens_out=200000)


def test_context_window_safe():
    client = LLMClient()
    # Should not raise
    client.check_context_window("gpt-4o", MESSAGES, max_tokens_out=1000)


def test_context_window_uses_input_limit_not_output_limit():
    """
    litellm's `max_tokens` is the maximum *output* length; `max_input_tokens` is
    the context window. Reading the wrong one made the pre-flight check reject
    payloads roughly 8x under gpt-4o's real limit (16384 instead of 128000).
    """
    info = litellm.model_cost["gpt-4o"]
    max_output = info["max_tokens"]
    max_context = info["max_input_tokens"]
    assert max_output < max_context, "fixture assumption: output limit is the smaller number"

    client = LLMClient()

    # Comfortably above the output limit but well within the context window:
    # this must be allowed.
    client.check_context_window("gpt-4o", MESSAGES, max_tokens_out=max_output + 1000)

    # Above the real context window: this must be rejected.
    with pytest.raises(ContextWindowExceededError) as exc:
        client.check_context_window("gpt-4o", MESSAGES, max_tokens_out=max_context + 1)
    assert str(max_context) in str(exc.value)


def test_context_window_falls_back_for_unknown_models():
    """Unknown models must not raise; they fall back to a conservative default."""
    client = LLMClient()
    assert litellm.model_cost.get("openrouter/some/unlisted-model:free") is None
    client.check_context_window(
        "openrouter/some/unlisted-model:free", MESSAGES, max_tokens_out=1000
    )
    with pytest.raises(ContextWindowExceededError):
        client.check_context_window(
            "openrouter/some/unlisted-model:free",
            MESSAGES,
            max_tokens_out=client.DEFAULT_MAX_CONTEXT + 1,
        )


def test_model_lookup_falls_back_to_bare_name():
    """A provider-prefixed model should still resolve via its bare name."""
    client = LLMClient()
    info = client._lookup_model_info("openai/gpt-4o")
    assert info.get("max_input_tokens") == litellm.model_cost["gpt-4o"]["max_input_tokens"]


def test_instructor_client_is_reused():
    """Built once per LLMClient rather than on every structured call."""
    client = LLMClient()
    first = client._get_instructor_client()
    assert client._get_instructor_client() is first

@pytest.mark.asyncio
async def test_generate_success():
    client = LLMClient()
    
    class MockMessage:
        content = "Hi there"
        
    class MockChoice:
        message = MockMessage()
        
    class MockUsage:
        prompt_tokens = 10
        completion_tokens = 5
        total_tokens = 15
        
    class MockResponse:
        choices = [MockChoice()]
        usage = MockUsage()
        model = "gpt-4o"
        
    with patch("litellm.acompletion", return_value=MockResponse()):
        with patch("litellm.completion_cost", return_value=0.002):
            res = await client.generate("gpt-4o", [{"role": "user", "content": "Hi"}])
            
            assert res["content"] == "Hi there"
            assert res["cost_usd"] == 0.002
            assert res["total_tokens"] == 15
            assert res["model_used"] == "gpt-4o"
