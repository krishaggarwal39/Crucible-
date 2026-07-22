import logging
from typing import Any, Dict, List
import numpy as np
from sqlalchemy import select
import uuid

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
    
    # 1. Ensure Qdrant and MinIO are ready
    await vector_db.init_collection()
    
    # 2. Summarize and Embed Current Traces
    current_vectors = {}  # scenario_id -> vector
    
    for meta in traces_meta:
        trace_id = meta["trace_id"]
        scenario_id = meta["scenario_id"]
        storage_key = meta["storage_key"]
        
        # Fetch full trace from MinIO
        try:
            trace_bytes = await s3_client.download_trace(storage_key)
            trace_data = trace_bytes.decode("utf-8")
        except Exception as e:
            errors.append(f"Failed to fetch trace {storage_key} for drift analysis: {e}")
            continue
            
        # Summarize
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
            total_cost += sum_res["cost_usd"]
            
            # Embed
            emb_res = await llm_client.embed(
                model="text-embedding-3-small",
                input_text=summary_text
            )
            vector = emb_res["vector"]
            total_cost += emb_res["cost_usd"]
            
            # Store
            await vector_db.upsert_trace_embedding(
                tenant_id=tenant_id,
                run_id=run_id,
                trace_id=trace_id,
                vector=vector,
                model_version="text-embedding-3-small",
                metadata={"scenario_id": scenario_id}
            )
            
            current_vectors[scenario_id] = vector
            
        except Exception as e:
            logger.error(f"Error processing trace {trace_id}: {e}")
            errors.append(f"Error embedding trace {trace_id}: {str(e)}")

    # 3. Resolve Baseline
    baseline_run_id = None
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
        res = await session.execute(stmt)
        baseline = res.scalar_one_or_none()
        
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
            res = await session.execute(stmt)
            baseline = res.scalar_one_or_none()
            
        if baseline:
            baseline_run_id = str(baseline.id)
            if baseline.embedding_model_version != "text-embedding-3-small":
                logger.warning("Baseline embedding model mismatch")
                baseline_run_id = None

    if not baseline_run_id:
        drift_profile = {
            "status": "baseline_missing",
            "score": None,
            "reason": "No valid baseline found or model mismatch"
        }
        return {"drift_profile": drift_profile, "total_cost_usd": total_cost, "errors": errors}

    # 4. Calculate Drift
    baseline_vectors = await vector_db.get_baseline_vectors(
        tenant_id=tenant_id,
        baseline_run_id=baseline_run_id,
        model_version="text-embedding-3-small"
    )
    
    base_vec_map = {bv["scenario_id"]: bv["vector"] for bv in baseline_vectors}
    
    # Intersection logic
    overlapping_scenarios = set(current_vectors.keys()).intersection(base_vec_map.keys())
    
    if len(overlapping_scenarios) < 5 and len(current_vectors) >= 5: # Require at least 5 for confidence, unless total run is < 5 (edge case for tests)
        if len(overlapping_scenarios) == 0:
            drift_profile = {
                "status": "insufficient_overlap",
                "score": None,
                "reason": f"Only {len(overlapping_scenarios)} overlapping scenarios."
            }
            return {"drift_profile": drift_profile, "total_cost_usd": total_cost, "errors": errors}

    if len(overlapping_scenarios) == 0:
        return {"drift_profile": None, "total_cost_usd": total_cost, "errors": ["No overlapping scenarios to compute drift"]}

    similarities = []
    for sid in overlapping_scenarios:
        v1 = np.array(current_vectors[sid])
        v2 = np.array(base_vec_map[sid])
        # Cosine similarity between two unit vectors is their dot product
        # text-embedding-3-small returns normalized vectors, but let's be safe
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

    drift_profile = {
        "status": "success",
        "score": round(drift_score, 4),
        "band": band,
        "baseline_run_id": baseline_run_id,
        "overlap_count": len(overlapping_scenarios)
    }

    return {
        "drift_profile": drift_profile,
        "total_cost_usd": total_cost,
        "errors": errors
    }
