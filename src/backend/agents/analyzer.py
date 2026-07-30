import asyncio
import logging
import uuid
from typing import Any, Dict

import numpy as np
from sqlalchemy import select

from backend.agents.state import EvaluationState
from backend.connectors.llm import LLMClient
from backend.connectors.s3 import S3BlobStore
from backend.core.config import get_settings
from backend.core.vector_db import VectorDB
from backend.db.models.evaluation_run import EvaluationRun
from backend.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)
settings = get_settings()

llm_client = LLMClient()
# Reads QDRANT_URL / QDRANT_API_KEY from settings by default.
vector_db = VectorDB()
s3_client = S3BlobStore()

# Minimum shared scenarios required before a drift number is meaningful.
MIN_OVERLAP_FOR_DRIFT = 5
BASELINE_MIN_SCORE = 80


async def _process_single_trace(meta: dict, tenant_id: str, run_id: str) -> dict:
    """Helper to download, summarize, embed, and store a single trace concurrently."""
    trace_id = meta["trace_id"]
    scenario_id = meta["scenario_id"]
    storage_key = meta.get("storage_key")

    if not storage_key:
        return {"error": f"Trace {trace_id} has no storage key; skipping drift analysis."}

    try:
        trace_bytes = await s3_client.download_trace(storage_key)
        trace_data = trace_bytes.decode("utf-8")
    except Exception as e:
        return {"error": f"Failed to fetch trace {storage_key} for drift analysis: {e}"}

    summary_prompt = (
        "Extract the agent's step-by-step strategy, tool usage sequence, and reasoning logic "
        "from the following trace, ignoring specific data values and timestamps.\n\n"
        f"TRACE:\n{trace_data}"
    )
    try:
        sum_res = await llm_client.generate(
            model=settings.DEFAULT_GENERATOR_MODEL,
            messages=[{"role": "user", "content": summary_prompt}],
            max_tokens=300,
        )
        summary_text = sum_res["content"]

        emb_res = await llm_client.embed(
            model=settings.DEFAULT_EMBEDDING_MODEL,
            input_text=summary_text,
        )
        vector = emb_res["vector"]

        await vector_db.upsert_trace_embedding(
            tenant_id=tenant_id,
            run_id=run_id,
            trace_id=trace_id,
            vector=vector,
            model_version=settings.DEFAULT_EMBEDDING_MODEL,
            metadata={"scenario_id": scenario_id},
        )

        return {
            "scenario_id": scenario_id,
            "vector": vector,
            "cost_usd": sum_res["cost_usd"] + emb_res["cost_usd"],
        }
    except Exception as e:
        logger.error(f"Error processing trace {trace_id}: {e}")
        return {"error": f"Error embedding trace {trace_id}: {str(e)}"}


async def _resolve_baseline(tenant_id: str, agent_id: str, run_id: str) -> str | None:
    """Helper to query the DB and find the correct Golden Baseline run ID."""
    async with AsyncSessionLocal() as session:
        # Pinned baseline. Excludes the current run so a run that has just been
        # flagged as a baseline is never compared against itself.
        stmt = (
            select(EvaluationRun)
            .where(
                EvaluationRun.tenant_id == uuid.UUID(tenant_id),
                EvaluationRun.agent_config_id == uuid.UUID(agent_id),
                EvaluationRun.is_baseline.is_(True),
                EvaluationRun.id != uuid.UUID(run_id),
                EvaluationRun.status == "completed",
            )
            .order_by(EvaluationRun.created_at.desc())
            .limit(1)
        )
        baseline = (await session.execute(stmt)).scalar_one_or_none()

        if not baseline:
            # Fallback to oldest successful run
            stmt = (
                select(EvaluationRun)
                .where(
                    EvaluationRun.tenant_id == uuid.UUID(tenant_id),
                    EvaluationRun.agent_config_id == uuid.UUID(agent_id),
                    EvaluationRun.avg_score >= BASELINE_MIN_SCORE,
                    EvaluationRun.id != uuid.UUID(run_id),
                    EvaluationRun.status == "completed",
                )
                .order_by(EvaluationRun.created_at.asc())
                .limit(1)
            )
            baseline = (await session.execute(stmt)).scalar_one_or_none()

        if baseline:
            if baseline.embedding_model_version != settings.DEFAULT_EMBEDDING_MODEL:
                logger.warning(
                    "Baseline %s was embedded with %r but the current model is %r; "
                    "vectors are not comparable.",
                    baseline.id,
                    baseline.embedding_model_version,
                    settings.DEFAULT_EMBEDDING_MODEL,
                )
                return None
            return str(baseline.id)
    return None


def _compute_drift_math(current_vectors: dict, baseline_vectors: list) -> dict:
    """
    Cosine-similarity drift between this run and its baseline.

    The previous branching made one arm unreachable and reported
    "insufficient_overlap" only when the overlap was exactly zero.
    """
    base_vec_map = {bv["scenario_id"]: bv["vector"] for bv in baseline_vectors}
    overlapping = set(current_vectors.keys()).intersection(base_vec_map.keys())
    overlap_count = len(overlapping)

    if overlap_count == 0:
        return {
            "status": "insufficient_overlap",
            "score": None,
            "overlap_count": 0,
            "reason": "No scenarios are shared with the baseline run.",
        }

    similarities = []
    for sid in overlapping:
        v1 = np.array(current_vectors[sid], dtype=float)
        v2 = np.array(base_vec_map[sid], dtype=float)
        denom = np.linalg.norm(v1) * np.linalg.norm(v2)
        if denom == 0:
            continue
        similarities.append(float(np.dot(v1, v2) / denom))

    if not similarities:
        return {
            "status": "insufficient_overlap",
            "score": None,
            "overlap_count": overlap_count,
            "reason": "Shared scenarios had zero-magnitude embeddings.",
        }

    mean_sim = sum(similarities) / len(similarities)
    drift_score = 1.0 - mean_sim

    if drift_score < 0.1:
        band = "Stable"
    elif drift_score < 0.25:
        band = "Minor Drift"
    else:
        band = "Significant Drift"

    result = {
        "status": "success",
        "score": round(drift_score, 4),
        "band": band,
        "overlap_count": overlap_count,
    }

    # Report low confidence instead of silently presenting a thin comparison.
    if overlap_count < MIN_OVERLAP_FOR_DRIFT:
        result["confidence"] = "low"
        result["reason"] = (
            f"Only {overlap_count} overlapping scenarios "
            f"(recommended minimum {MIN_OVERLAP_FOR_DRIFT})."
        )
    else:
        result["confidence"] = "normal"

    return result


async def analyze_drift_node(state: EvaluationState) -> Dict[str, Any]:
    """
    Computes behavioral drift by embedding trace summaries and comparing them
    to a Golden Baseline in Qdrant.
    """
    tenant_id = state["tenant_id"]
    agent_id = state["agent_id"]
    run_id = state["run_id"]
    traces_meta = state.get("traces", [])

    if not traces_meta:
        return {"drift_profile": None, "errors": ["No traces available for analysis"]}

    total_cost = 0.0
    errors = []

    await vector_db.init_collection()

    semaphore = asyncio.Semaphore(max(1, settings.NODE_CONCURRENCY_LIMIT))

    async def _bounded(meta):
        async with semaphore:
            return await _process_single_trace(meta, tenant_id, run_id)

    results = await asyncio.gather(
        *[_bounded(meta) for meta in traces_meta], return_exceptions=True
    )

    current_vectors = {}
    for res in results:
        if isinstance(res, BaseException):
            errors.append(str(res))
        elif "error" in res:
            errors.append(res["error"])
        else:
            current_vectors[res["scenario_id"]] = res["vector"]
            total_cost += res["cost_usd"]

    if not current_vectors:
        return {
            "drift_profile": {
                "status": "no_embeddings",
                "score": None,
                "reason": "No trace could be embedded, so drift cannot be computed.",
            },
            "total_cost_usd": total_cost,
            "errors": errors,
        }

    baseline_run_id = await _resolve_baseline(tenant_id, agent_id, run_id)

    if not baseline_run_id:
        drift_profile = {
            "status": "baseline_missing",
            "score": None,
            "reason": "No valid baseline found or embedding model mismatch",
        }
        return {
            "drift_profile": drift_profile,
            "total_cost_usd": total_cost,
            "errors": errors,
        }

    baseline_vectors = await vector_db.get_baseline_vectors(
        tenant_id=tenant_id,
        baseline_run_id=baseline_run_id,
        model_version=settings.DEFAULT_EMBEDDING_MODEL,
    )

    drift_result = _compute_drift_math(current_vectors, baseline_vectors)
    drift_result["baseline_run_id"] = baseline_run_id

    return {
        "drift_profile": drift_result,
        "total_cost_usd": total_cost,
        "errors": errors,
    }
