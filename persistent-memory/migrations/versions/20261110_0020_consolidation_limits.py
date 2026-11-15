"""Share consolidation preview quotas across API replicas."""

from alembic import op

revision = "20261110_0020"
down_revision = "20261110_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE memory_write_rate_limit DROP CONSTRAINT memory_write_rate_limit_operation_check"
    )
    op.execute(
        "ALTER TABLE memory_write_rate_limit ADD CONSTRAINT memory_write_rate_limit_operation_check "
        "CHECK (operation IN ('event','command','consolidation'))"
    )


def downgrade() -> None:
    op.execute("DELETE FROM memory_write_rate_limit WHERE operation='consolidation'")
    op.execute(
        "ALTER TABLE memory_write_rate_limit DROP CONSTRAINT memory_write_rate_limit_operation_check"
    )
    op.execute(
        "ALTER TABLE memory_write_rate_limit ADD CONSTRAINT memory_write_rate_limit_operation_check "
        "CHECK (operation IN ('event','command'))"
    )
