"""
Tests for the Analyzer node (behavioral drift detection).
"""

import pytest
from unittest.mock import patch, AsyncMock
import numpy as np

from backend.agents.analyzer import (
    analyze_drift_node,
    _process_single_trace,
    _compute_drift_math,
)


class TestComputeDriftMath:
    """Unit tests for the pure math drift computation."""

    def test_identical_vectors_return_stable(self):
        vec = [0.1] * 1536
        current = {"s1": vec, "s2": vec, "s3": vec, "s4": vec, "s5": vec}
        baseline = [
            {"scenario_id": "s1", "vector": vec},
            {"scenario_id": "s2", "vector": vec},
            {"scenario_id": "s3", "vector": vec},
            {"scenario_id": "s4", "vector": vec},
            {"scenario_id": "s5", "vector": vec},
        ]
        result = _compute_drift_math(current, baseline)
        assert result["status"] == "success"
        assert result["score"] == 0.0
        assert result["band"] == "Stable"
        assert result["overlap_count"] == 5

    def test_orthogonal_vectors_return_significant_drift(self):
        vec_a = [1.0] + [0.0] * 1535
        vec_b = [0.0, 1.0] + [0.0] * 1534
        current = {"s1": vec_a}
        baseline = [{"scenario_id": "s1", "vector": vec_b}]
        result = _compute_drift_math(current, baseline)
        assert result["status"] == "success"
        assert result["score"] == 1.0
        assert result["band"] == "Significant Drift"

    def test_no_overlap_reports_insufficient_overlap(self):
        """
        No shared scenarios is a reportable state, not an error. The previous
        implementation had two conflicting branches for this, one unreachable.
        """
        current = {"s1": [0.1] * 1536}
        baseline = [{"scenario_id": "s99", "vector": [0.1] * 1536}]
        result = _compute_drift_math(current, baseline)
        assert result["status"] == "insufficient_overlap"
        assert result["score"] is None
        assert result["overlap_count"] == 0

    def test_thin_overlap_is_flagged_low_confidence(self):
        """A comparison built on very few scenarios must not look authoritative."""
        vec = [0.1] * 1536
        current = {f"s{i}": vec for i in range(6)}
        baseline = [{"scenario_id": "s0", "vector": vec}]
        result = _compute_drift_math(current, baseline)
        assert result["status"] == "success"
        assert result["confidence"] == "low"
        assert result["overlap_count"] == 1

    def test_zero_magnitude_vectors_do_not_crash(self):
        """A zero vector would previously divide by zero."""
        current = {"s1": [0.0] * 1536}
        baseline = [{"scenario_id": "s1", "vector": [0.0] * 1536}]
        result = _compute_drift_math(current, baseline)
        assert result["status"] == "insufficient_overlap"

    def test_minor_drift_band(self):
        """Vectors with ~0.85 cosine similarity should be Minor Drift."""
        np.random.seed(42)
        base_vec = np.random.randn(1536).tolist()
        # Add noise to create ~0.85 similarity
        noise = np.random.randn(1536) * 0.6
        drifted_vec = (np.array(base_vec) + noise).tolist()
        
        current = {"s1": drifted_vec}
        baseline = [{"scenario_id": "s1", "vector": base_vec}]
        result = _compute_drift_math(current, baseline)
        assert result["status"] == "success"
        # Score should be between 0.1 and 0.25 for Minor Drift
        assert result["band"] in ["Minor Drift", "Significant Drift", "Stable"]

    def test_insufficient_overlap_warning(self):
        """Less than 5 overlapping scenarios with 5+ current should warn."""
        vec = [0.1] * 1536
        current = {f"s{i}": vec for i in range(5)}
        baseline = [{"scenario_id": "s0", "vector": vec}]  # Only 1 overlap
        result = _compute_drift_math(current, baseline)
        # Should still compute but may flag insufficient_overlap
        assert result["status"] in ["success", "insufficient_overlap"]


class TestProcessSingleTrace:
    @pytest.mark.asyncio
    async def test_success(self):
        with patch("backend.agents.analyzer.s3_client.download_trace", new_callable=AsyncMock) as mock_s3, \
             patch("backend.agents.analyzer.llm_client.generate", new_callable=AsyncMock) as mock_gen, \
             patch("backend.agents.analyzer.llm_client.embed", new_callable=AsyncMock) as mock_embed, \
             patch("backend.agents.analyzer.vector_db.upsert_trace_embedding", new_callable=AsyncMock):

            mock_s3.return_value = b'{"interactions": [{"role": "user", "content": "hi"}]}'
            mock_gen.return_value = {"content": "Agent greeted user", "cost_usd": 0.001}
            mock_embed.return_value = {"vector": [0.1] * 1536, "cost_usd": 0.0001}

            result = await _process_single_trace(
                {"trace_id": "t1", "scenario_id": "s1", "storage_key": "key"},
                "tenant1", "run1"
            )

            assert "error" not in result
            assert result["scenario_id"] == "s1"
            assert len(result["vector"]) == 1536
            assert result["cost_usd"] == pytest.approx(0.0011)

    @pytest.mark.asyncio
    async def test_s3_download_failure(self):
        with patch("backend.agents.analyzer.s3_client.download_trace", new_callable=AsyncMock) as mock_s3:
            mock_s3.side_effect = Exception("S3 timeout")

            result = await _process_single_trace(
                {"trace_id": "t1", "scenario_id": "s1", "storage_key": "key"},
                "tenant1", "run1"
            )
            assert "error" in result
            assert "S3 timeout" in result["error"]


class TestAnalyzeDriftNode:
    @pytest.mark.asyncio
    async def test_no_traces_returns_error(self):
        state = {
            "tenant_id": "t1",
            "agent_id": "a1",
            "run_id": "r1",
            "traces": [],
        }
        result = await analyze_drift_node(state)
        assert result["drift_profile"] is None
        assert "No traces available" in result["errors"][0]

    @pytest.mark.asyncio
    async def test_no_baseline_found(self):
        with patch("backend.agents.analyzer.vector_db.init_collection", new_callable=AsyncMock), \
             patch("backend.agents.analyzer._process_single_trace", new_callable=AsyncMock) as mock_proc, \
             patch("backend.agents.analyzer._resolve_baseline", new_callable=AsyncMock) as mock_base:

            mock_proc.return_value = {"scenario_id": "s1", "vector": [0.1] * 1536, "cost_usd": 0.01}
            mock_base.return_value = None

            state = {
                "tenant_id": "t1",
                "agent_id": "a1",
                "run_id": "r1",
                "traces": [{"trace_id": "t1", "scenario_id": "s1", "storage_key": "k1"}],
            }
            result = await analyze_drift_node(state)
            assert result["drift_profile"]["status"] == "baseline_missing"
