import logging
from typing import Any, Dict, List

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models

from backend.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Payload fields we filter on. Without indexes, every baseline lookup is a full
# collection scan.
_INDEXED_PAYLOAD_FIELDS = ("tenant_id", "run_id", "embedding_model_version", "scenario_id")


class VectorDB:
    def __init__(
        self,
        url: str | None = None,
        api_key: str | None = None,
        collection_name: str | None = None,
        vector_size: int | None = None,
    ):
        # Default to configuration rather than a hardcoded localhost URL. The old
        # default meant QDRANT_URL was never honoured, so a containerised worker
        # tried to reach its own localhost.
        self.url = url or settings.QDRANT_URL
        self.api_key = api_key if api_key is not None else (settings.QDRANT_API_KEY or None)
        self.collection_name = collection_name or settings.QDRANT_COLLECTION
        self.vector_size = vector_size or settings.EMBEDDING_DIMENSIONS

        self.client = AsyncQdrantClient(url=self.url, api_key=self.api_key)
        self._initialized = False

    async def init_collection(self) -> None:
        """Create the collection and payload indexes if they don't exist."""
        if self._initialized:
            return
        try:
            collections = await self.client.get_collections()
            collection_names = [c.name for c in collections.collections]
            if self.collection_name not in collection_names:
                logger.info(f"Creating Qdrant collection: {self.collection_name}")
                await self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=models.VectorParams(
                        # Driven by EMBEDDING_DIMENSIONS so the collection can
                        # never silently disagree with the embedding model.
                        size=self.vector_size,
                        distance=models.Distance.COSINE,
                    ),
                )

            for field in _INDEXED_PAYLOAD_FIELDS:
                try:
                    await self.client.create_payload_index(
                        collection_name=self.collection_name,
                        field_name=field,
                        field_schema=models.PayloadSchemaType.KEYWORD,
                    )
                except Exception:
                    # Already exists — Qdrant has no create-if-not-exists here.
                    pass

            self._initialized = True
        except Exception as e:
            logger.error(f"Error initializing Qdrant collection: {e}")

    async def verify_dimensions(self) -> None:
        """
        Raise if the live collection's vector size disagrees with configuration.

        This guards the exact failure that made drift analysis silently useless:
        the collection was created at 768 for one embedding model, then the model
        was changed without updating the collection.
        """
        info = await self.client.get_collection(self.collection_name)
        params = info.config.params
        vectors = params.vectors
        actual = vectors.size if hasattr(vectors, "size") else None
        if actual is not None and actual != self.vector_size:
            raise ValueError(
                f"Qdrant collection {self.collection_name!r} has vector size {actual} "
                f"but EMBEDDING_DIMENSIONS is {self.vector_size}. Recreate the "
                f"collection or set EMBEDDING_DIMENSIONS to match the embedding model."
            )

    async def upsert_trace_embedding(
        self,
        tenant_id: str,
        run_id: str,
        trace_id: str,
        vector: List[float],
        model_version: str,
        metadata: Dict[str, Any],
    ):
        """Upserts a trace embedding into Qdrant."""
        if len(vector) != self.vector_size:
            raise ValueError(
                f"Embedding has {len(vector)} dimensions but the collection expects "
                f"{self.vector_size}. Check DEFAULT_EMBEDDING_MODEL / EMBEDDING_DIMENSIONS."
            )
        try:
            payload = {
                "tenant_id": tenant_id,
                "run_id": run_id,
                "trace_id": trace_id,
                "embedding_model_version": model_version,
                **metadata,
            }
            await self.client.upsert(
                collection_name=self.collection_name,
                points=[
                    models.PointStruct(
                        id=trace_id,  # trace ids are UUIDs, which Qdrant accepts
                        vector=vector,
                        payload=payload,
                    )
                ],
            )
        except Exception as e:
            logger.error(f"Error upserting trace embedding: {e}")
            raise

    async def get_baseline_vectors(
        self,
        tenant_id: str,
        baseline_run_id: str,
        model_version: str,
    ) -> List[Dict[str, Any]]:
        """
        Fetch all vectors for a baseline run, paging through the whole result set.

        The previous implementation used a single scroll with limit=1000 and
        ignored the next-page offset, so any baseline with more traces than that
        was silently truncated.
        """
        scroll_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="tenant_id", match=models.MatchValue(value=tenant_id)
                ),
                models.FieldCondition(
                    key="run_id", match=models.MatchValue(value=baseline_run_id)
                ),
                models.FieldCondition(
                    key="embedding_model_version",
                    match=models.MatchValue(value=model_version),
                ),
            ]
        )

        baseline_vectors: List[Dict[str, Any]] = []
        offset = None
        try:
            while True:
                records, next_offset = await self.client.scroll(
                    collection_name=self.collection_name,
                    scroll_filter=scroll_filter,
                    with_vectors=True,
                    limit=settings.QDRANT_SCROLL_PAGE_SIZE,
                    offset=offset,
                )
                for record in records:
                    if record.vector:
                        baseline_vectors.append({
                            "id": record.id,
                            "vector": record.vector,
                            "scenario_id": (record.payload or {}).get("scenario_id"),
                        })
                if next_offset is None:
                    break
                offset = next_offset
            return baseline_vectors
        except Exception as e:
            logger.error(f"Error fetching baseline vectors: {e}")
            return baseline_vectors

    async def close(self) -> None:
        try:
            await self.client.close()
        except Exception as e:
            logger.warning(f"Error closing Qdrant client: {e}")
