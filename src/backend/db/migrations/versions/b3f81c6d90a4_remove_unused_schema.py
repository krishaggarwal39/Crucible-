"""remove unused schema: golden_baselines table and unimplemented connector types

Two pieces of schema that no code path ever reaches:

1. golden_baselines — an 8-column table with three indexes that is never INSERTed
   into and never queried. The "pin a run as the reference" feature is implemented
   with EvaluationRun.is_baseline instead, and _resolve_baseline reads that flag.
   Verified 0 rows before dropping.

2. ConnectorType.SDK / ConnectorType.MCP — selectable in the UI but the simulator
   raised NotImplementedError for both, so choosing either produced a run that was
   guaranteed to fail. Verified 0 rows use them (all agent_configs are REST_API).
   They can be re-added when the connectors actually exist.

Revision ID: b3f81c6d90a4
Revises: 9c1f4a7b2e10
Create Date: 2026-07-30

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b3f81c6d90a4'
down_revision: Union[str, Sequence[str], None] = '9c1f4a7b2e10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_CONNECTOR_VALUES = ('REST_API', 'SDK', 'MCP')
NEW_CONNECTOR_VALUES = ('REST_API',)


def _replace_enum(name: str, values: tuple[str, ...], table: str, column: str) -> None:
    """
    Narrow or widen a Postgres enum.

    Postgres cannot remove a value from an existing enum, so the type has to be
    rebuilt and the column re-cast through text.
    """
    tmp = f"{name}_new"
    sa.Enum(*values, name=tmp).create(op.get_bind())
    op.execute(
        f'ALTER TABLE {table} ALTER COLUMN {column} '
        f'TYPE {tmp} USING {column}::text::{tmp}'
    )
    op.execute(f'DROP TYPE {name}')
    op.execute(f'ALTER TYPE {tmp} RENAME TO {name}')


def upgrade() -> None:
    # ── 1. Drop the unused golden_baselines table ────────────────────────────
    op.drop_index('ix_golden_baselines_tenant_id', table_name='golden_baselines')
    op.drop_index('ix_golden_baselines_evaluation_run_id', table_name='golden_baselines')
    op.drop_index('ix_golden_baselines_agent_config_id', table_name='golden_baselines')
    op.drop_table('golden_baselines')

    # ── 2. Narrow connector_type_enum to the one implemented connector ───────
    _replace_enum(
        'connector_type_enum', NEW_CONNECTOR_VALUES, 'agent_configs', 'connector_type'
    )


def downgrade() -> None:
    # ── 2. Restore the wider enum ────────────────────────────────────────────
    _replace_enum(
        'connector_type_enum', OLD_CONNECTOR_VALUES, 'agent_configs', 'connector_type'
    )

    # ── 1. Recreate golden_baselines as it stood at revision 14f34cc264ce ────
    op.create_table(
        'golden_baselines',
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('agent_config_id', sa.Uuid(), nullable=False),
        sa.Column('evaluation_run_id', sa.Uuid(), nullable=False),
        sa.Column('approved_by', sa.Uuid(), nullable=True),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('behavioral_embedding_collection', sa.String(length=255), nullable=False),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['agent_config_id'], ['agent_configs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['approved_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['evaluation_run_id'], ['evaluation_runs.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_golden_baselines_agent_config_id', 'golden_baselines', ['agent_config_id'])
    op.create_index('ix_golden_baselines_evaluation_run_id', 'golden_baselines', ['evaluation_run_id'])
    op.create_index('ix_golden_baselines_tenant_id', 'golden_baselines', ['tenant_id'])

    # Silence the unused-import warning for postgresql, kept for symmetry with
    # the rest of the migration chain.
    _ = postgresql
