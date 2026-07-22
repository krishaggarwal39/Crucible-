import pytest
from unittest.mock import patch, MagicMock

from litellm.exceptions import RateLimitError

from backend.connectors.llm import LLMClient, ContextWindowExceededError

def test_context_window_exceeded():
    client = LLMClient()
    
    # gpt-4o usually has 128k context
    # Send a tiny message, but ask for 200,000 tokens out
    messages = [{"role": "user", "content": "Hello"}]
    
    with pytest.raises(ContextWindowExceededError, match="exceeds model gpt-4o limit"):
        client.check_context_window("gpt-4o", messages, max_tokens_out=200000)
        
def test_context_window_safe():
    client = LLMClient()
    messages = [{"role": "user", "content": "Hello"}]
    
    # Should not raise
    client.check_context_window("gpt-4o", messages, max_tokens_out=1000)

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
