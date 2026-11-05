"""Index worker cleanup predicates across scopes."""

from alembic import op

revision = "20261105_0011"
down_revision = "20261105_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_memory_write_rate_limit_window",
        "memory_write_rate_limit",
        ["window_started_at"],
    )
    op.create_index(
        "ix_memory_recall_diagnostic_selected_ids",
        "memory_recall_diagnostic",
        ["selected_memory_ids"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_memory_recall_diagnostic_selected_ids")
    op.drop_index("ix_memory_write_rate_limit_window")
