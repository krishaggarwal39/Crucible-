"""Add last_heartbeat_at to EvaluationRun

Revision ID: db25dbfda68e
Revises: 14f34cc264ce
Create Date: 2026-07-21 23:51:56.122279

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'db25dbfda68e'
down_revision: Union[str, Sequence[str], None] = '14f34cc264ce'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # NOTE: the two foreign-key statements below were emitted BACKWARDS by
    # autogenerate. The model declared ondelete="CASCADE" while the database had
    # none, and autogenerate resolved that difference in the wrong direction —
    # dropping the cascade on upgrade and adding it on downgrade. That is the
    # root cause of the long-standing agent_configs cascade drift.
    #
    # They are left in place because this revision has already been applied to
    # existing databases; revision 9c1f4a7b2e10 restores the cascade properly.
    op.drop_constraint(op.f('agent_configs_tenant_id_fkey'), 'agent_configs', type_='foreignkey')
    op.create_foreign_key(None, 'agent_configs', 'tenants', ['tenant_id'], ['id'])
    op.add_column('evaluation_runs', sa.Column('last_heartbeat_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('evaluation_runs', 'last_heartbeat_at')
    # Was `op.drop_constraint(None, ...)`, which raises because Alembic cannot
    # drop an unnamed constraint. Postgres auto-names the constraint created by
    # the upgrade above 'agent_configs_tenant_id_fkey', so name it explicitly.
    op.drop_constraint('agent_configs_tenant_id_fkey', 'agent_configs', type_='foreignkey')
    op.create_foreign_key(
        'agent_configs_tenant_id_fkey',
        'agent_configs',
        'tenants',
        ['tenant_id'],
        ['id'],
        ondelete='CASCADE',
    )
