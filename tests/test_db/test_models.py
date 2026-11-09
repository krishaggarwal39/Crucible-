"""
Integration tests for Milestone 2 — Database Models & Alembic.

Tests:
1. Tables exist in Postgres with correct columns
2. Tenant → cascade deletes propagate to all child tables
3. Enum values are correctly stored and retrieved
4. Judgment.passed + score round-trip correctly

Run: pytest tests/test_db/test_models.py -v
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import get_settings
from backend.db.models import (
    AgentConfig,
    EvaluationRun,
    Judgment,
    Scenario,
    Tenant,
    TraceMetadata,
    User,
)
from backend.db.models.agent_config import ConnectorType
from backend.db.models.evaluation_run import RunStatus
from backend.db.models.scenario import ScenarioSeverity
from backend.db.models.trace_metadata import TraceStatus
from backend.db.models.user import UserRole

settings = get_settings()


# ── Fixtures ──────────────────────────────────────────────────────────────────

# Removed duplicated fixtures, now using test_engine and test_session from conftest.py


# ── Helpers ───────────────────────────────────────────────────────────────────

async def make_tenant(session: AsyncSession, slug: str = "test-corp") -> Tenant:
    tenant = Tenant(name="Test Corp", slug=slug)
    session.add(tenant)
    await session.flush()
    return tenant


async def make_user(session: AsyncSession, tenant: Tenant) -> User:
    user = User(
        tenant_id=tenant.id,
        email=f"user_{uuid.uuid4().hex[:6]}@example.com",
        hashed_password="$2b$12$fakehash",
        role=UserRole.ADMIN,
    )
    session.add(user)
    await session.flush()
    return user


async def make_agent(session: AsyncSession, tenant: Tenant, user: User) -> AgentConfig:
    agent = AgentConfig(
        tenant_id=tenant.id,
        created_by=user.id,
        name="Test Agent",
        connector_type=ConnectorType.REST_API,
        endpoint_url="http://localhost:8001/agent",
    )
    session.add(agent)
    await session.flush()
    return agent


async def make_run(
    session: AsyncSession, tenant: Tenant, agent: AgentConfig, user: User
) -> EvaluationRun:
    run = EvaluationRun(
        tenant_id=tenant.id,
        agent_config_id=agent.id,
        created_by=user.id,
        name="Test Run",
        status=RunStatus.PENDING,
        run_config={"scenario_count": 10},
    )
    session.add(run)
    await session.flush()
    return run


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestTablesExist:
    async def test_all_tables_created(self, test_engine):
        """All expected tables must exist in the database."""
        expected = {
            "tenants", "users", "agent_configs", "evaluation_runs",
            "scenarios", "trace_metadata", "judgments",
        }
        async with test_engine.connect() as conn:
            tables = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_table_names()
            )
        assert expected.issubset(set(tables)), f"Missing tables: {expected - set(tables)}"

    async def test_alembic_version_at_head(self, test_engine):
        """alembic_version must exist and contain exactly one row."""
        async with test_engine.connect() as conn:
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            rows = result.fetchall()
        assert len(rows) == 1, "Expected exactly one alembic version row"


class TestCascadeDelete:
    async def test_tenant_delete_cascades_to_all_children(self, test_session):
        """Deleting a tenant must cascade to users, agent_configs, evaluation_runs, scenarios."""
        session = test_session
        tenant = await make_tenant(session)
        user = await make_user(session, tenant)
        agent = await make_agent(session, tenant, user)
        run = await make_run(session, tenant, agent, user)

        scenario = Scenario(
            evaluation_run_id=run.id,
            title="Test Scenario",
            description="A test",
            category="prompt_injection",
            severity=ScenarioSeverity.HIGH,
            input_payload={"messages": [{"role": "user", "content": "Hello"}]},
            expected_behavior="Should refuse",
        )
        session.add(scenario)
        await session.flush()

        tenant_id = tenant.id
        await session.delete(tenant)
        await session.commit()

        # Verify cascade — query the same session (it has fresh state after commit)
        result = await session.execute(
            text("SELECT COUNT(*) FROM users WHERE tenant_id = :tid"),
            {"tid": tenant_id},
        )
        assert result.scalar() == 0, "Users should have been cascade deleted"

        result = await session.execute(
            text("SELECT COUNT(*) FROM agent_configs WHERE tenant_id = :tid"),
            {"tid": tenant_id},
        )
        assert result.scalar() == 0, "AgentConfigs should have been cascade deleted"

        result = await session.execute(
            text("SELECT COUNT(*) FROM evaluation_runs WHERE tenant_id = :tid"),
            {"tid": tenant_id},
        )
        assert result.scalar() == 0, "EvaluationRuns should have been cascade deleted"

        result = await session.execute(
            text("SELECT COUNT(*) FROM scenarios s JOIN evaluation_runs r ON s.evaluation_run_id = r.id WHERE r.tenant_id = :tid"),
            {"tid": tenant_id},
        )
        assert result.scalar() == 0, "Scenarios should have been cascade deleted"


class TestEnumRoundTrip:
    async def test_user_role_stored_and_retrieved(self, test_session):
        session = test_session
        tenant = await make_tenant(session)
        user = User(
            tenant_id=tenant.id,
            email="admin@example.com",
            hashed_password="$2b$12$hash",
            role=UserRole.ADMIN,
        )
        session.add(user)
        await session.commit()
        user_id = user.id

        # Expire and re-fetch to prove DB round-trip
        session.expire(user)
        fetched = await session.get(User, user_id)
        assert fetched is not None
        assert fetched.role == UserRole.ADMIN

    async def test_run_status_transitions(self, test_session):
        session = test_session
        tenant = await make_tenant(session)
        user = await make_user(session, tenant)
        agent = await make_agent(session, tenant, user)
        run = await make_run(session, tenant, agent, user)
        run_id = run.id
        await session.commit()

        session.expire(run)
        run = await session.get(EvaluationRun, run_id)
        assert run.status == RunStatus.PENDING
        run.status = RunStatus.RUNNING
        await session.commit()

        session.expire(run)
        run = await session.get(EvaluationRun, run_id)
        assert run.status == RunStatus.RUNNING


class TestJudgmentScores:
    async def test_judgment_score_round_trip(self, test_session):
        session = test_session
        tenant = await make_tenant(session)
        user = await make_user(session, tenant)
        agent = await make_agent(session, tenant, user)
        run = await make_run(session, tenant, agent, user)

        scenario = Scenario(
            evaluation_run_id=run.id,
            title="Score Test",
            description="Testing score storage",
            category="correctness",
            severity=ScenarioSeverity.LOW,
            input_payload={"messages": []},
            expected_behavior="Some behavior",
        )
        session.add(scenario)
        await session.flush()

        trace = TraceMetadata(
            scenario_id=scenario.id,
            evaluation_run_id=run.id,
            storage_key="s3://crucible-traces/test/trace.json",
            token_count=500,
            duration_ms=1200,
            turn_count=3,
            tool_call_count=1,
            status=TraceStatus.COMPLETED,
            completed_at=datetime.now(timezone.utc),
        )
        session.add(trace)
        await session.flush()

        judgment = Judgment(
            trace_id=trace.id,
            scenario_id=scenario.id,
            evaluation_run_id=run.id,
            overall_score=87.5,
            safety_score=95.0,
            correctness_score=82.0,
            instruction_following_score=85.5,
            passed=True,
            reasoning="The agent responded safely and correctly.",
            raw_output={"choices": [{"message": {"content": "..."}}]},
            judge_model="gpt-4o",
        )
        session.add(judgment)
        await session.commit()
        judgment_id = judgment.id

        session.expire(judgment)
        j = await session.get(Judgment, judgment_id)
        assert j is not None
        assert j.overall_score == 87.5
        assert j.passed is True
        assert j.judge_model == "gpt-4o"
        assert j.reasoning is not None

    async def test_duplicate_judgment_violates_constraint(self, test_session):
        from sqlalchemy.exc import IntegrityError
        
        session = test_session
        tenant = await make_tenant(session)
        user = await make_user(session, tenant)
        agent = await make_agent(session, tenant, user)
        run = await make_run(session, tenant, agent, user)

        scenario = Scenario(
            evaluation_run_id=run.id,
            title="Duplicate Test",
            description="Testing duplicate judgment",
            category="correctness",
            severity=ScenarioSeverity.LOW,
            input_payload={"messages": []},
            expected_behavior="Some behavior",
        )
        session.add(scenario)
        await session.flush()

        trace = TraceMetadata(
            scenario_id=scenario.id,
            evaluation_run_id=run.id,
            storage_key="s3://crucible-traces/test/trace_dup.json",
            token_count=500,
            duration_ms=1200,
            turn_count=3,
            tool_call_count=1,
            status=TraceStatus.COMPLETED,
            completed_at=datetime.now(timezone.utc),
        )
        session.add(trace)
        await session.flush()

        judgment1 = Judgment(
            trace_id=trace.id,
            scenario_id=scenario.id,
            evaluation_run_id=run.id,
            overall_score=87.5,
            safety_score=95.0,
            correctness_score=82.0,
            instruction_following_score=85.5,
            passed=True,
            reasoning="First judgment.",
            raw_output={"choices": [{"message": {"content": "..."}}]},
            judge_model="gpt-4o",
        )
        session.add(judgment1)
        await session.flush()

        judgment2 = Judgment(
            trace_id=trace.id,
            scenario_id=scenario.id,
            evaluation_run_id=run.id,
            overall_score=90.0,
            safety_score=90.0,
            correctness_score=90.0,
            instruction_following_score=90.0,
            passed=True,
            reasoning="Second duplicate judgment.",
            raw_output={},
            judge_model="gpt-4o",
        )
        session.add(judgment2)
        
        with pytest.raises(IntegrityError):
            await session.commit()


class TestRemovedSchema:
    """
    Guards the dead-schema removal in revision b3f81c6d90a4.

    golden_baselines was an 8-column table with three indexes that was never
    written to or read: the baseline feature is implemented with
    EvaluationRun.is_baseline. ConnectorType.SDK / MCP were selectable in the UI
    but the simulator raised NotImplementedError for both.
    """

    async def test_golden_baselines_table_is_gone(self, test_engine):
        async with test_engine.connect() as conn:
            tables = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_table_names()
            )
        assert "golden_baselines" not in tables

    async def test_golden_baseline_model_is_not_exported(self):
        import backend.db.models as models

        assert not hasattr(models, "GoldenBaseline")
        assert "GoldenBaseline" not in models.__all__

    async def test_connector_enum_only_offers_implemented_transports(self):
        from backend.db.models.agent_config import ConnectorType

        assert [c.value for c in ConnectorType] == ["rest_api"]

    async def test_db_connector_enum_matches_the_python_enum(self, test_engine):
        """The Postgres type must be narrowed too, not just the Python enum."""
        async with test_engine.connect() as conn:
            result = await conn.execute(text(
                "SELECT e.enumlabel FROM pg_enum e "
                "JOIN pg_type t ON t.oid = e.enumtypid "
                "WHERE t.typname = 'connector_type_enum' ORDER BY e.enumsortorder"
            ))
            labels = [r[0] for r in result.fetchall()]
        assert labels == ["REST_API"], f"unexpected enum labels: {labels}"
