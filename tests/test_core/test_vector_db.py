"""
Tests for the VectorDB (Qdrant) wrapper.
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from backend.core.vector_db import VectorDB


TEST_DIMS = 1536


@pytest.fixture
def vector_db():
    with patch("backend.core.vector_db.AsyncQdrantClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        # Explicit vector_size so these tests do not depend on the configured
        # embedding model's dimensionality.
        db = VectorDB(url="http://fake:6333", vector_size=TEST_DIMS)
        db.client = mock_client
        yield db, mock_client


class TestVectorDB:
    @pytest.mark.asyncio
    async def test_init_collection_creates_if_missing(self, vector_db):
        db, mock_client = vector_db

        # Simulate empty collections
        mock_collections = MagicMock()
        mock_collections.collections = []
        mock_client.get_collections.return_value = mock_collections

        await db.init_collection()

        mock_client.create_collection.assert_called_once()
        call_kwargs = mock_client.create_collection.call_args[1]
        assert call_kwargs["collection_name"] == "behavioral_traces"

    @pytest.mark.asyncio
    async def test_init_collection_skips_if_exists(self, vector_db):
        db, mock_client = vector_db

        mock_collection = MagicMock()
        mock_collection.name = "behavioral_traces"
        mock_collections = MagicMock()
        mock_collections.collections = [mock_collection]
        mock_client.get_collections.return_value = mock_collections

        await db.init_collection()

        mock_client.create_collection.assert_not_called()

    @pytest.mark.asyncio
    async def test_upsert_trace_embedding(self, vector_db):
        db, mock_client = vector_db

        await db.upsert_trace_embedding(
            tenant_id="t1",
            run_id="r1",
            trace_id="trace-abc",
            vector=[0.1] * 1536,
            model_version="text-embedding-3-small",
            metadata={"scenario_id": "s1"},
        )

        mock_client.upsert.assert_called_once()
        call_kwargs = mock_client.upsert.call_args[1]
        assert call_kwargs["collection_name"] == "behavioral_traces"
        point = call_kwargs["points"][0]
        assert point.id == "trace-abc"
        assert len(point.vector) == 1536
        assert point.payload["tenant_id"] == "t1"

    @pytest.mark.asyncio
    async def test_get_baseline_vectors(self, vector_db):
        db, mock_client = vector_db

        mock_record = MagicMock()
        mock_record.id = "rec-1"
        mock_record.vector = [0.5] * 1536
        mock_record.payload = {"scenario_id": "s1"}
        mock_client.scroll.return_value = ([mock_record], None)

        results = await db.get_baseline_vectors(
            tenant_id="t1",
            baseline_run_id="r-base",
            model_version="text-embedding-3-small",
        )

        assert len(results) == 1
        assert results[0]["id"] == "rec-1"
        assert results[0]["scenario_id"] == "s1"
        assert len(results[0]["vector"]) == 1536

    @pytest.mark.asyncio
    async def test_get_baseline_vectors_error_returns_empty(self, vector_db):
        db, mock_client = vector_db
        mock_client.scroll.side_effect = Exception("Connection refused")

        results = await db.get_baseline_vectors("t1", "r-base", "model")
        assert results == []

    @pytest.mark.asyncio
    async def test_upsert_error_raises(self, vector_db):
        db, mock_client = vector_db
        mock_client.upsert.side_effect = Exception("Qdrant down")

        with pytest.raises(Exception, match="Qdrant down"):
            await db.upsert_trace_embedding(
                tenant_id="t1",
                run_id="r1",
                trace_id="t1",
                vector=[0.1] * TEST_DIMS,
                model_version="v1",
                metadata={},
            )

    @pytest.mark.asyncio
    async def test_upsert_rejects_wrong_dimensionality(self, vector_db):
        """
        Guards the failure that made drift analysis silently useless: the
        collection was built for one embedding size and the model later changed,
        so every upsert was mismatched.
        """
        db, mock_client = vector_db

        with pytest.raises(ValueError, match="dimensions"):
            await db.upsert_trace_embedding(
                tenant_id="t1",
                run_id="r1",
                trace_id="t1",
                vector=[0.1] * 10,
                model_version="v1",
                metadata={},
            )
        mock_client.upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_collection_uses_configured_dimensions(self, vector_db):
        db, mock_client = vector_db
        mock_collections = MagicMock()
        mock_collections.collections = []
        mock_client.get_collections.return_value = mock_collections

        await db.init_collection()

        vectors_config = mock_client.create_collection.call_args[1]["vectors_config"]
        assert vectors_config.size == TEST_DIMS

    @pytest.mark.asyncio
    async def test_get_baseline_vectors_pages_through_all_results(self, vector_db):
        """
        A single scroll with a hard limit silently truncated large baselines.
        Verify every page is consumed.
        """
        db, mock_client = vector_db

        def record(rid):
            rec = MagicMock()
            rec.id = rid
            rec.vector = [0.5] * TEST_DIMS
            rec.payload = {"scenario_id": f"s-{rid}"}
            return rec

        mock_client.scroll.side_effect = [
            ([record("a"), record("b")], "offset-2"),
            ([record("c")], None),
        ]

        results = await db.get_baseline_vectors("t1", "r-base", "model")

        assert [r["id"] for r in results] == ["a", "b", "c"]
        assert mock_client.scroll.call_count == 2
