import asyncio
import logging
from uuid import UUID

from celery import shared_task
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from backend.worker.celery_app import celery_app
from backend.worker.utils import run_async_graph
from backend.db.session import AsyncSessionLocal
from backend.db.models import EvaluationRun, AgentConfig, Scenario, TraceMetadata, Judgment
from backend.agents.orchestrator import build_evaluation_graph
from backend.core.events import EventBus, RedisEventPublisher
from backend.schemas.events import EvaluationEventV1
from backend.core.telemetry import current_run_id
from backend.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

async def _execute_evaluation_run_async(run_id: str):
    logger.info(f"Starting evaluation run {run_id}")
    
    async with AsyncSessionLocal() as session:
        # Fetch the EvaluationRun with its AgentConfig
        stmt = select(EvaluationRun).options(selectinload(EvaluationRun.agent_config)).where(EvaluationRun.id == UUID(run_id))
        result = await session.execute(stmt)
        run: EvaluationRun = result.scalar_one_or_none()
        
        if not run:
            logger.error(f"EvaluationRun {run_id} not found.")
            return

        # Atomic Ownership Acquisition: `pending` -> `running`
        from sqlalchemy import update
        stmt = (
            update(EvaluationRun)
            .where(EvaluationRun.id == UUID(run_id), EvaluationRun.status == "pending")
            .values(status="running")
        )
        update_result = await session.execute(stmt)
        if update_result.rowcount == 0:
            logger.warning(f"Run {run_id} is no longer pending or already claimed. Aborting execution.")
            return
            
        await session.commit()
        
        # Reload the run to get the updated status and config
        stmt_reload = select(EvaluationRun).options(selectinload(EvaluationRun.agent_config)).where(EvaluationRun.id == UUID(run_id))
        result = await session.execute(stmt_reload)
        run = result.scalar_one_or_none()
        
        from celery import current_task
        run.celery_task_id = current_task.request.id if current_task else None
        await session.commit()
        
        tenant_id_str = str(run.tenant_id)
        agent_id_str = str(run.agent_config.id)
        max_budget_usd = run.run_config.get("eval_budget_usd", 5.0)
        target_endpoint_url = run.agent_config.endpoint_url
        connector_type = run.agent_config.connector_type.value

        # Prepare initial state for the orchestrator
        initial_state = {
            "run_id": run_id,
            "tenant_id": tenant_id_str,
            "agent_id": agent_id_str,
            "config": {
                "max_budget_usd": max_budget_usd,
                "target_endpoint_url": target_endpoint_url,
                "connector_type": connector_type,
                "target_description": run.agent_config.description or "An AI agent",
                "target_system_prompt": run.agent_config.system_prompt or "You are a helpful assistant",
            },
            "total_cost_usd": 0.0,
            "turn_count": 0,
            "scenarios": [],
            "traces": [],
            "judgments": [],
            "evolution_suggestions": [],
            "drift_profile": {},
            "errors": [],
        }

        # Setup Event Bus
        event_bus = None
        heartbeat_task = None
        
        try:
            event_bus = EventBus(RedisEventPublisher())
            seq_num = 1
            
            # Background task for heartbeat
            heartbeat_task = asyncio.create_task(_heartbeat_loop(run_id))
            
            # Set OpenTelemetry/Logging context
            current_run_id.set(run_id)

            logger.info(f"Invoking orchestrator graph for {run_id}")
            graph = build_evaluation_graph()
            
            # Emit starting event
            await event_bus.publish_evaluation_event(
                EvaluationEventV1(
                    seq_num=seq_num,
                    run_id=run_id,
                    status="running",
                    current_node="start"
                )
            )
            seq_num += 1
            
            final_state = initial_state
            current_status = "running"
            _cancelled = False
            import time
            last_cancel_check = time.time()
            
            async for event in graph.astream(initial_state, stream_mode="updates"):
                # Poll database for cancellation (throttled to every 5 seconds)
                if time.time() - last_cancel_check > 5.0:
                    async with AsyncSessionLocal() as check_session:
                        stmt_cancel = select(EvaluationRun.status).where(EvaluationRun.id == UUID(run_id))
                        current_status = await check_session.scalar(stmt_cancel)
                    last_cancel_check = time.time()
                    
                if current_status == "cancelled":
                    logger.info(f"Run {run_id} cancelled by user during execution.")
                    final_state.setdefault("errors", []).append("Run cancelled by user.")
                    _cancelled = True
                    break
                    
                # LangGraph `updates` stream returns a dict mapping node_name to state update
                for node_name, state_update in event.items():
                    # Accumulate state internally
                    final_state.update(state_update)
                    
                    # Publish snapshot event
                    await event_bus.publish_evaluation_event(
                        EvaluationEventV1(
                            seq_num=seq_num,
                            run_id=run_id,
                            status="running",
                            current_node=node_name,
                            turn_count=final_state.get("turn_count", 0),
                            total_cost_usd=final_state.get("total_cost_usd", 0.0),
                            passed_scenarios=sum(1 for j in final_state.get("judgments", []) if j.get("passed")),
                            failed_scenarios=sum(1 for j in final_state.get("judgments", []) if not j.get("passed"))
                        )
                    )
                    seq_num += 1
            # Persist the output centrally
            async with AsyncSessionLocal() as save_session:
                stmt_reload = select(EvaluationRun).options(selectinload(EvaluationRun.agent_config)).where(EvaluationRun.id == UUID(run_id))
                run = (await save_session.execute(stmt_reload)).scalar_one_or_none()
                if run:
                    await _persist_final_state(save_session, run, final_state)
                    await save_session.commit()
            
            # Publish completion/cancellation event
            final_status = "cancelled" if _cancelled else "completed"
            await event_bus.publish_evaluation_event(
                EvaluationEventV1(
                    seq_num=seq_num,
                    run_id=run_id,
                    status=final_status,
                    current_node="end",
                    turn_count=final_state.get("turn_count", 0),
                    total_cost_usd=final_state.get("total_cost_usd", 0.0),
                    passed_scenarios=sum(1 for j in final_state.get("judgments", []) if j.get("passed")),
                    failed_scenarios=sum(1 for j in final_state.get("judgments", []) if not j.get("passed"))
                )
            )
            
        except Exception as e:
            logger.error(f"Run {run_id} failed: {e}", exc_info=True)
            run._fatal_error = True
            run.error_message = str(e)
            
            # Publish terminal failure event
            await event_bus.publish_evaluation_event(
                EvaluationEventV1(
                    seq_num=seq_num,
                    run_id=run_id,
                    status="failed",
                    current_node="error",
                    error_message=str(e)
                )
            )
            
        finally:
            if event_bus:
                await event_bus.close()
            
            # Cancel the heartbeat task
            if heartbeat_task:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
            
            # Stale Writer Rejection: Only transition to terminal state if still 'running' or 'cancelled'
            async with AsyncSessionLocal() as terminal_session:
                stmt = (
                    update(EvaluationRun)
                    .where(
                        EvaluationRun.id == UUID(run_id), 
                        EvaluationRun.status.in_(["running", "cancelled"])
                    )
                    .values(
                        status="cancelled" if _cancelled else ("failed" if hasattr(run, "_fatal_error") else "completed"),
                        error_message=run.error_message if hasattr(run, "_fatal_error") else None
                    )
                )
                update_result = await terminal_session.execute(stmt)
                if update_result.rowcount == 0:
                    logger.error(f"Failed to commit terminal state for Run {run_id}. Worker was likely marked stale by the sweeper.")
                else:
                    await terminal_session.commit()
                    logger.info(f"Run {run_id} finished execution.")

async def _heartbeat_loop(run_id: str):
    """Periodically updates the last_heartbeat_at timestamp in the database."""
    from datetime import datetime, timezone
    interval = settings.HEARTBEAT_INTERVAL_SECONDS
    try:
        while True:
            await asyncio.sleep(interval)
            try:
                async with AsyncSessionLocal() as session:
                    stmt = select(EvaluationRun).where(EvaluationRun.id == UUID(run_id))
                    result = await session.execute(stmt)
                    run = result.scalar_one_or_none()
                    if run and run.status == "running":
                        run.last_heartbeat_at = datetime.now(timezone.utc)
                        await session.commit()
            except Exception as e:
                logger.warning(f"Heartbeat DB error for run {run_id}: {e}")
    except asyncio.CancelledError:
        pass

async def _persist_final_state(session: AsyncSession, run: EvaluationRun, final_state: dict):
    # 1. Scenarios
    scenarios_data = final_state.get("scenarios", [])
    scenario_models = {}
    for s in scenarios_data:
        scenario = Scenario(
            evaluation_run_id=run.id,
            title=s.get("title", "Unknown"),
            description=s.get("description", ""),
            expected_behavior=s.get("expected_behavior", ""),
            category=s.get("category", "general"),
            input_payload=s.get("input_payload", {})
        )
        session.add(scenario)
        scenario_models[s.get("id")] = scenario
    
    await session.flush() # Flush to get scenario IDs
    
    # 2. Traces
    traces_data = final_state.get("traces", [])
    trace_models_by_key = {}
    for t in traces_data:
        s_id = t.get("scenario_id")
        scenario = scenario_models.get(s_id)
        
        trace = TraceMetadata(
            id=UUID(t["trace_id"]),
            evaluation_run_id=run.id,
            scenario_id=scenario.id if scenario else None,
            storage_key=t["storage_key"],
            turn_count=t.get("turn_count", 0),
            token_count=0, # Aggregate if available
            duration_ms=0,
            tool_call_count=0,
            error_message=t.get("error_message")
        )
        session.add(trace)
        trace_models_by_key[t["storage_key"]] = trace
        
    await session.flush()

    # 3. Judgments
    judgments_data = final_state.get("judgments", [])
    passed_count = 0
    failed_count = 0
    for j in judgments_data:
        trace = trace_models_by_key.get(j.get("trace_key"))
        if not trace:
            continue
            
        judgment = Judgment(
            evaluation_run_id=run.id,
            scenario_id=trace.scenario_id,
            trace_id=trace.id,
            passed=j.get("passed", False),
            overall_score=j.get("score", 0.0),
            reasoning=j.get("reasoning"),
            judge_model="gpt-4o"
        )
        session.add(judgment)
        
        if judgment.passed:
            passed_count += 1
        else:
            failed_count += 1

    # Atomic increment of scores
    total = passed_count + failed_count
    run.passed_scenarios = EvaluationRun.passed_scenarios + passed_count
    run.failed_scenarios = EvaluationRun.failed_scenarios + failed_count
    run.total_scenarios = EvaluationRun.total_scenarios + total
    
    # Compute average score from judgments
    scores = [j.get("score", 0.0) for j in judgments_data if j.get("trace_key") in trace_models_by_key]
    if scores:
        run.avg_score = sum(scores) / len(scores)
    
    run.drift_profile = final_state.get("drift_profile")
    run.evolution_suggestions = final_state.get("evolution_suggestions")
    run.embedding_model_version = "text-embedding-3-small"


@shared_task(bind=True)
def execute_evaluation_run(self, run_id: str):
    """
    Celery task that acts as the entrypoint for an evaluation run.
    """
    run_async_graph(_execute_evaluation_run_async(run_id))

@shared_task(bind=True)
def delete_s3_traces(self, run_id: str, tenant_id: str):
    """
    Celery task to physically remove all traces associated with a run from S3.
    """
    from backend.connectors.s3 import S3BlobStore
    
    async def _delete():
        s3 = S3BlobStore()
        prefix = f"tenants/{tenant_id}/runs/{run_id}/"
        try:
            await s3.delete_prefix(prefix)
            logger.info(f"Successfully deleted all traces in S3 under {prefix}")
        except Exception as e:
            logger.error(f"Failed to delete S3 traces for {run_id}: {e}")
        finally:
            await s3.close()
            
    run_async_graph(_delete())
