import logging
from typing import Any, Dict, List
import numpy as np
from sqlalchemy import select
import uuid
import asyncio

from backend.agents.state import EvaluationState
from backend.connectors.llm import LLMClient
from backend.core.vector_db import VectorDB
from backend.connectors.s3 import S3BlobStore
from backend.db.session import AsyncSessionLocal
from backend.db.models.evaluation_run import EvaluationRun

logger = logging.getLogger(__name__)

llm_client = LLMClient()
vector_db = VectorDB()
s3_client = S3BlobStore()

async def _process_single_trace(meta: dict, tenant_id: str, run_id: str) -> dict:
    """Helper to download, summarize, embed, and store a single trace concurrently."""
    trace_id = meta["trace_id"]
    scenario_id = meta["scenario_id"]
    storage_key = meta["storage_key"]
    
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
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": summary_prompt}],
            max_tokens=300
        )
        summary_text = sum_res["content"]
        
        emb_res = await llm_client.embed(
            model="text-embedding-3-small",
            input_text=summary_text
        )
        vector = emb_res["vector"]
        
        await vector_db.upsert_trace_embedding(
            tenant_id=tenant_id,
            run_id=run_id,
            trace_id=trace_id,
            vector=vector,
            model_version="text-embedding-3-small",
            metadata={"scenario_id": scenario_id}
        )
        
        return {
            "scenario_id": scenario_id,
            "vector": vector,
            "cost_usd": sum_res["cost_usd"] + emb_res["cost_usd"]
        }
    except Exception as e:
        logger.error(f"Error processing trace {trace_id}: {e}")
        return {"error": f"Error embedding trace {trace_id}: {str(e)}"}


async def _resolve_baseline(tenant_id: str, agent_id: str, run_id: str) -> str | None:
    """Helper to query the DB and find the correct Golden Baseline run ID."""
    async with AsyncSessionLocal() as session:
        # Pinned baseline
        stmt = (
            select(EvaluationRun)
            .where(
                EvaluationRun.tenant_id == uuid.UUID(tenant_id),
                EvaluationRun.agent_config_id == uuid.UUID(agent_id),
                EvaluationRun.is_baseline == True
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
                    EvaluationRun.avg_score >= 80,
                    EvaluationRun.id != uuid.UUID(run_id)
                )
                .order_by(EvaluationRun.created_at.asc())
                .limit(1)
            )
            baseline = (await session.execute(stmt)).scalar_one_or_none()
            
        if baseline:
            if baseline.embedding_model_version != "text-embedding-3-small":
                logger.warning("Baseline embedding model mismatch")
                return None
            return str(baseline.id)
    return None


def _compute_drift_math(current_vectors: dict, baseline_vectors: list) -> dict:
    """Helper to perform cosine similarity math."""
    base_vec_map = {bv["scenario_id"]: bv["vector"] for bv in baseline_vectors}
    overlapping_scenarios = set(current_vectors.keys()).intersection(base_vec_map.keys())
    
    if len(overlapping_scenarios) < 5 and len(current_vectors) >= 5:
        if len(overlapping_scenarios) == 0:
            return {
                "status": "insufficient_overlap",
                "score": None,
                "reason": f"Only {len(overlapping_scenarios)} overlapping scenarios."
            }

    if len(overlapping_scenarios) == 0:
        return {"error": "No overlapping scenarios to compute drift"}

    similarities = []
    for sid in overlapping_scenarios:
        v1 = np.array(current_vectors[sid])
        v2 = np.array(base_vec_map[sid])
        sim = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2))
        similarities.append(float(sim))
        
    mean_sim = sum(similarities) / len(similarities)
    drift_score = 1.0 - mean_sim
    
    if drift_score < 0.1:
        band = "Stable"
    elif drift_score < 0.25:
        band = "Minor Drift"
    else:
        band = "Significant Drift"

    return {
        "status": "success",
        "score": round(drift_score, 4),
        "band": band,
        "overlap_count": len(overlapping_scenarios)
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

    total_cost = 0.0
    errors = []
    
    await vector_db.init_collection()
    
    # Process all traces concurrently
    tasks = [_process_single_trace(meta, tenant_id, run_id) for meta in traces_meta]
    results = await asyncio.gather(*tasks)
    
    current_vectors = {}
    for res in results:
        if "error" in res:
            errors.append(res["error"])
        else:
            current_vectors[res["scenario_id"]] = res["vector"]
            total_cost += res["cost_usd"]

    # Resolve baseline
    baseline_run_id = await _resolve_baseline(tenant_id, agent_id, run_id)

    if not baseline_run_id:
        drift_profile = {
            "status": "baseline_missing",
            "score": None,
            "reason": "No valid baseline found or model mismatch"
        }
        return {"drift_profile": drift_profile, "total_cost_usd": total_cost, "errors": errors}

    # Fetch baseline vectors
    baseline_vectors = await vector_db.get_baseline_vectors(
        tenant_id=tenant_id,
        baseline_run_id=baseline_run_id,
        model_version="text-embedding-3-small"
    )
    
    # Compute Drift
    drift_result = _compute_drift_math(current_vectors, baseline_vectors)
    
    if "error" in drift_result:
        errors.append(drift_result["error"])
        drift_profile = None
    else:
        drift_profile = drift_result
        drift_profile["baseline_run_id"] = baseline_run_id

    return {
        "drift_profile": drift_profile,
        "total_cost_usd": total_cost,
        "errors": errors
    }
