"""restore ON DELETE CASCADE on agent_configs.tenant_id

The model has declared ForeignKey("tenants.id", ondelete="CASCADE") since
commit 2271f06 ("fix(db): add CASCADE delete to AgentConfig"), but no migration
was ever generated for it. Migration db25dbfda68e additionally dropped the
constraint and recreated it *without* ondelete, so the live schema has had
NO ACTION the whole time and deleting a tenant raised ForeignKeyViolation.

Alembic autogenerate does not detect ondelete changes on existing foreign keys,
which is why this drifted silently. This migration makes the database match the
model.

Revision ID: 9c1f4a7b2e10
Revises: 0e258e8b9c04
Create Date: 2026-07-30

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '9c1f4a7b2e10'
down_revision: Union[str, Sequence[str], None] = '0e258e8b9c04'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONSTRAINT_NAME = "agent_configs_tenant_id_fkey"


def upgrade() -> None:
    """Recreate the FK with ON DELETE CASCADE."""
    op.drop_constraint(CONSTRAINT_NAME, "agent_configs", type_="foreignkey")
    op.create_foreign_key(
        CONSTRAINT_NAME,
        "agent_configs",
        "tenants",
        ["tenant_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    """Restore the previous NO ACTION behaviour."""
    op.drop_constraint(CONSTRAINT_NAME, "agent_configs", type_="foreignkey")
    op.create_foreign_key(
        CONSTRAINT_NAME,
        "agent_configs",
        "tenants",
        ["tenant_id"],
        ["id"],
    )
