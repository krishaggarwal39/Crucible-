"""
AI Operations API routes — drift analytics and system health metrics.
"""

import logging
from typing import List

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
            # "paired" compares matched scenarios; "centroid" compares the overall
            # behavioural distribution because scenario ids are regenerated each
            # run. Exposed so the dashboard can label the weaker measurement
            # rather than presenting both as equivalent.
            "method": profile.get("method"),
            "confidence": profile.get("confidence"),
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

    # Mean *judgment* score across runs that have drift data.
    stmt_avg = select(func.avg(EvaluationRun.avg_score)).where(
        EvaluationRun.tenant_id == tenant_id,
        EvaluationRun.status == "completed",
        EvaluationRun.drift_profile.isnot(None),
    )
    avg_quality_score = (await db.execute(stmt_avg)).scalar()

    stmt_runs = select(EvaluationRun).where(
        EvaluationRun.tenant_id == tenant_id,
        EvaluationRun.status == "completed",
        EvaluationRun.drift_profile.isnot(None),
    )
    result = await db.execute(stmt_runs)
    all_drift_runs = result.scalars().all()

    # The real mean drift score, read from the drift profiles themselves.
    drift_scores = [
        r.drift_profile["score"]
        for r in all_drift_runs
        if r.drift_profile and isinstance(r.drift_profile.get("score"), (int, float))
    ]
    avg_drift_score = (
        round(sum(drift_scores) / len(drift_scores), 4) if drift_scores else None
    )

    # Count anomalous runs (drift score > 0.25)
    anomalous_count = sum(1 for s in drift_scores if s > 0.25)

    # Baseline stability — how many agents have a pinned baseline
    stmt_baselines = select(func.count(EvaluationRun.id)).where(
        EvaluationRun.tenant_id == tenant_id,
        EvaluationRun.is_baseline.is_(True),
    )
    baseline_count = (await db.execute(stmt_baselines)).scalar() or 0

    return {
        # Previously `avg_drift_score` was computed from EvaluationRun.avg_score —
        # the judge's quality score, not drift at all. Both are now reported
        # under accurate names.
        "avg_drift_score": avg_drift_score,
        "avg_quality_score": round(avg_quality_score, 1) if avg_quality_score else None,
        "anomalous_runs": anomalous_count,
        "total_drift_runs": len(all_drift_runs),
        "drift_alert_count": anomalous_count,
        "baseline_count": baseline_count,
    }
