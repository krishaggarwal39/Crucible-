"""
A graph run that raises nothing can still have produced nothing.

Nodes accumulate problems into state["errors"] rather than raising, so the graph
completes "successfully" with an empty result set. Observed live: the generator hit
OpenRouter's free-tier daily limit and the run was recorded as
status=COMPLETED, total_scenarios=0, error_message=NULL — indistinguishable from a
real success.
"""

import pytest

from backend.worker.tasks import _pipeline_failed, _summarise_pipeline_errors


class TestPipelineFailed:
    def test_no_scenarios_and_no_judgments_is_a_failure(self):
        state = {"scenarios": [], "judgments": [], "errors": ["Scenario generation failed: boom"]}
        assert _pipeline_failed(state) is not None

    def test_empty_run_with_no_errors_is_still_a_failure(self):
        """Silence plus no output is not success."""
        state = {"scenarios": [], "judgments": [], "errors": []}
        msg = _pipeline_failed(state)
        assert msg is not None
        assert "no scenarios or judgments" in msg

    def test_scenarios_but_no_judgments_is_a_failure(self):
        state = {"scenarios": [], "judgments": [], "errors": ["judge exploded"]}
        assert _pipeline_failed(state) is not None

    def test_partial_results_are_not_a_failure(self):
        """Some judgments plus some errors is a degraded success, not a failure."""
        state = {
            "scenarios": [{"id": "s1"}, {"id": "s2"}],
            "judgments": [{"passed": True}],
            "errors": ["Scenario s2: target agent timed out"],
        }
        assert _pipeline_failed(state) is None

    def test_full_success_is_not_a_failure(self):
        state = {
            "scenarios": [{"id": "s1"}],
            "judgments": [{"passed": True}],
            "errors": [],
        }
        assert _pipeline_failed(state) is None

    def test_scenarios_generated_but_nothing_judged_yet_is_not_flagged(self):
        """Scenarios alone count as output; the run is degraded, not empty."""
        state = {"scenarios": [{"id": "s1"}], "judgments": [], "errors": ["judge failed"]}
        assert _pipeline_failed(state) is None


class TestSummarisePipelineErrors:
    @pytest.mark.parametrize("raw", [
        'Scenario generation failed: litellm.RateLimitError: {"error":{"message":"Rate limit exceeded: free-models-per-day"}}',
        "OpenrouterException - 429 Too Many Requests",
        "insufficient_quota for this account",
        "Provider quota exhausted",
    ])
    def test_rate_limit_gets_an_actionable_message(self, raw):
        msg = _summarise_pipeline_errors([raw])
        assert "rate limit or quota" in msg.lower()
        assert "retry later" in msg.lower()

    def test_provider_json_payload_is_not_leaked(self):
        """
        error_message is returned to API clients, and provider payloads embed
        nested JSON plus account identifiers.
        """
        raw = 'Judge failed: SomeError - {"error":{"user_id":"user_3CXE3Dfw","key":"sk-secret"}}'
        msg = _summarise_pipeline_errors([raw])
        assert "{" not in msg
        assert "user_3CXE3Dfw" not in msg
        assert "sk-secret" not in msg

    def test_generic_error_is_summarised_and_bounded(self):
        msg = _summarise_pipeline_errors(["x" * 900])
        assert len(msg) < 300

    def test_empty_error_list_is_handled(self):
        assert _summarise_pipeline_errors([]) != ""
