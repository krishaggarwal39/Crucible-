"""
AI Operations API routes — drift analytics and system health metrics.
"""

import logging
from typing import Any, List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from backend.api.deps import CurrentAdmin, get_db
from backend.db.models import EvaluationRun

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/operations", tags=["AI Operations"])


@router.get("/drift", response_model=List[dict])
async def get_drift_overview(
    current_user: CurrentAdmin,
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns drift profiles for recent completed runs belonging to the tenant.
    Used by the Operations dashboard for the PCA scatter plot.
    """
    stmt = (
        select(EvaluationRun)
        .where(
            EvaluationRun.tenant_id == current_user.tenant_id,
            EvaluationRun.status == "completed",
            EvaluationRun.drift_profile.isnot(None),
        )
        .order_by(EvaluationRun.created_at.desc())
        .limit(limit)
    )

    result = await db.execute(stmt)
    runs = result.scalars().all()

    # Transform into chart-friendly format
    drift_data = []
    for i, run in enumerate(runs):
        profile = run.drift_profile or {}
        score = profile.get("score")
        if score is None:
            continue

        drift_data.append({
            "run_id": str(run.id),
            "run_name": run.name,
            "drift_score": score,
            "band": profile.get("band", "Unknown"),
            "overlap_count": profile.get("overlap_count", 0),
            "created_at": run.created_at.isoformat() if run.created_at else None,
            # Simple 2D layout using index + score for visualization
            # (real PCA would require the actual vectors)
            "x": i,
            "y": score,
        })

    return drift_data


@router.get("/summary")
async def get_operations_summary(
    current_user: CurrentAdmin,
    db: AsyncSession = Depends(get_db),
):
    """
    Returns aggregated AI operations metrics for the tenant dashboard.
    """
    tenant_id = current_user.tenant_id

    # Average drift score across all runs with drift data
    stmt_avg = select(func.avg(EvaluationRun.avg_score)).where(
        EvaluationRun.tenant_id == tenant_id,
        EvaluationRun.status == "completed",
        EvaluationRun.drift_profile.isnot(None),
    )
    avg_score = (await db.execute(stmt_avg)).scalar()

    # Count anomalous runs (drift score > 0.25)
    stmt_runs = select(EvaluationRun).where(
        EvaluationRun.tenant_id == tenant_id,
        EvaluationRun.status == "completed",
        EvaluationRun.drift_profile.isnot(None),
    )
    result = await db.execute(stmt_runs)
    all_drift_runs = result.scalars().all()

    anomalous_count = sum(
        1
        for r in all_drift_runs
        if r.drift_profile and r.drift_profile.get("score", 0) > 0.25
    )

    # Baseline stability — how many agents have a pinned baseline
    stmt_baselines = select(func.count(EvaluationRun.id)).where(
        EvaluationRun.tenant_id == tenant_id,
        EvaluationRun.is_baseline == True,
    )
    baseline_count = (await db.execute(stmt_baselines)).scalar() or 0

    return {
        "avg_drift_score": round(avg_score, 1) if avg_score else None,
        "anomalous_runs": anomalous_count,
        "total_drift_runs": len(all_drift_runs),
        "baseline_count": baseline_count,
    }
