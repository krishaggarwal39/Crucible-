import logging
from typing import Any, Dict, List, Optional
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models

logger = logging.getLogger(__name__)

class VectorDB:
    def __init__(self, url: str = "http://localhost:6333"):
        self.client = AsyncQdrantClient(url=url)
        self.collection_name = "behavioral_traces"

    async def init_collection(self):
        """Creates the collection if it doesn't exist."""
        try:
            collections = await self.client.get_collections()
            collection_names = [c.name for c in collections.collections]
            if self.collection_name not in collection_names:
                logger.info(f"Creating Qdrant collection: {self.collection_name}")
                await self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=models.VectorParams(
                        size=768,  # gemini/text-embedding-004 dimensions
                        distance=models.Distance.COSINE
                    )
                )
        except Exception as e:
            logger.error(f"Error initializing Qdrant collection: {e}")

    async def upsert_trace_embedding(
        self,
        tenant_id: str,
        run_id: str,
        trace_id: str,
        vector: List[float],
        model_version: str,
        metadata: Dict[str, Any]
    ):
        """Upserts a trace embedding into Qdrant."""
        try:
            payload = {
                "tenant_id": tenant_id,
                "run_id": run_id,
                "trace_id": trace_id,
                "embedding_model_version": model_version,
                **metadata
            }
            await self.client.upsert(
                collection_name=self.collection_name,
                points=[
                    models.PointStruct(
                        id=trace_id,  # UUID maps well to Qdrant ID if valid, else need hash
                        vector=vector,
                        payload=payload
                    )
                ]
            )
        except Exception as e:
            logger.error(f"Error upserting trace embedding: {e}")
            raise

    async def get_baseline_vectors(
        self, 
        tenant_id: str, 
        baseline_run_id: str, 
        model_version: str
    ) -> List[Dict[str, Any]]:
        """
        Fetches all vectors for a given baseline run, ensuring tenant and model version match.
        """
        try:
            records, _ = await self.client.scroll(
                collection_name=self.collection_name,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="tenant_id",
                            match=models.MatchValue(value=tenant_id)
                        ),
                        models.FieldCondition(
                            key="run_id",
                            match=models.MatchValue(value=baseline_run_id)
                        ),
                        models.FieldCondition(
                            key="embedding_model_version",
                            match=models.MatchValue(value=model_version)
                        )
                    ]
                ),
                with_vectors=True,
                limit=1000
            )
            
            baseline_vectors = []
            for record in records:
                if record.vector:
                    baseline_vectors.append({
                        "id": record.id,
                        "vector": record.vector,
                        "scenario_id": record.payload.get("scenario_id")
                    })
            return baseline_vectors
        except Exception as e:
            logger.error(f"Error fetching baseline vectors: {e}")
            return []
