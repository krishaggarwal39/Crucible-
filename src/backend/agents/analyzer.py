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
            dimensions=settings.EMBEDDING_DIMENSIONS,
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


def _cosine(a, b) -> float | None:
    v1 = np.asarray(a, dtype=float)
    v2 = np.asarray(b, dtype=float)
    denom = float(np.linalg.norm(v1) * np.linalg.norm(v2))
    if denom == 0.0:
        return None
    return float(np.dot(v1, v2) / denom)


def _band(drift_score: float) -> str:
    if drift_score < 0.1:
        return "Stable"
    if drift_score < 0.25:
        return "Minor Drift"
    return "Significant Drift"


def _compute_drift_math(current_vectors: dict, baseline_vectors: list) -> dict:
    """
    Behavioural drift between this run and its baseline.

    Two methods, in order of preference:

    "paired" — cosine distance per shared scenario_id, averaged. This is the
    strongest signal because it compares like with like, but it only applies when
    the two runs actually share scenario ids.

    "centroid" — cosine distance between the mean embedding of each run. Used
    when there is no scenario overlap.

    Why the fallback is necessary: the generator mints a fresh uuid4 for every
    scenario on every run, so two independent runs never share a scenario_id and
    the paired overlap is always zero by construction. Keying drift solely on
    scenario_id therefore meant it could never produce a number, no matter how
    well embeddings worked. The centroid comparison is a distributional signal
    (has the agent's overall behaviour moved?) rather than a per-scenario one, so
    it is reported with its own method and confidence rather than being passed off
    as an equivalent measurement.
    """
    base_vec_map = {
        bv["scenario_id"]: bv["vector"]
        for bv in baseline_vectors
        if bv.get("scenario_id") and bv.get("vector")
    }
    baseline_all = [bv["vector"] for bv in baseline_vectors if bv.get("vector")]

    if not current_vectors or not baseline_all:
        return {
            "status": "insufficient_data",
            "score": None,
            "method": None,
            "overlap_count": 0,
            "reason": "Either this run or the baseline has no usable embeddings.",
        }

    overlapping = set(current_vectors) & set(base_vec_map)
    overlap_count = len(overlapping)

    # ── Preferred: paired per-scenario comparison ─────────────────────────────
    if overlap_count:
        sims = [
            s for sid in overlapping
            if (s := _cosine(current_vectors[sid], base_vec_map[sid])) is not None
        ]
        if sims:
            drift_score = 1.0 - (sum(sims) / len(sims))
            result = {
                "status": "success",
                "method": "paired",
                "score": round(drift_score, 4),
                "band": _band(drift_score),
                "overlap_count": overlap_count,
                "baseline_sample_size": len(baseline_all),
            }
            if overlap_count < MIN_OVERLAP_FOR_DRIFT:
                result["confidence"] = "low"
                result["reason"] = (
                    f"Only {overlap_count} overlapping scenarios "
                    f"(recommended minimum {MIN_OVERLAP_FOR_DRIFT})."
                )
            else:
                result["confidence"] = "normal"
            return result

    # ── Fallback: distributional centroid comparison ──────────────────────────
    current_centroid = np.mean(
        np.asarray(list(current_vectors.values()), dtype=float), axis=0
    )
    baseline_centroid = np.mean(np.asarray(baseline_all, dtype=float), axis=0)

    if current_centroid.shape != baseline_centroid.shape:
        return {
            "status": "dimension_mismatch",
            "score": None,
            "method": None,
            "overlap_count": 0,
            "reason": (
                f"This run's embeddings are {current_centroid.shape[0]}-dimensional but the "
                f"baseline's are {baseline_centroid.shape[0]}. The embedding model or "
                f"EMBEDDING_DIMENSIONS changed since the baseline was recorded."
            ),
        }

    sim = _cosine(current_centroid, baseline_centroid)
    if sim is None:
        return {
            "status": "insufficient_data",
            "score": None,
            "method": None,
            "overlap_count": 0,
            "reason": "Embeddings had zero magnitude.",
        }

    drift_score = 1.0 - sim
    return {
        "status": "success",
        "method": "centroid",
        "score": round(drift_score, 4),
        "band": _band(drift_score),
        "overlap_count": 0,
        "current_sample_size": len(current_vectors),
        "baseline_sample_size": len(baseline_all),
        "confidence": "low",
        "reason": (
            "No scenarios are shared with the baseline (scenarios are regenerated "
            "each run), so this compares the overall behavioural distribution "
            "rather than matched scenarios."
        ),
    }


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

    # Short-circuit before doing any work if embeddings are not usable — e.g. no
    # embedding provider key is configured. Drift is the only feature that needs
    # them, so the rest of the run must not be penalised. Previously this failed
    # once per trace and buried the real reason in a list of per-trace errors.
    if not settings.embeddings_enabled:
        reason = settings.embeddings_disabled_reason or "Embeddings are not configured."
        logger.info("Skipping drift analysis: %s", reason)
        return {
            "drift_profile": {
                "status": "embeddings_disabled",
                "score": None,
                "reason": reason,
            },
            "total_cost_usd": 0.0,
        }

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
