"""Index the reverse lookups used by event revocation and memory deletion."""

from alembic import op

revision = "20261111_0024"
down_revision = "20261111_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_memory_evidence_event_id", "memory_evidence", ["event_id"])
    op.create_index(
        "ix_candidate_decision_stored_memory_id",
        "memory_candidate_decision",
        ["stored_memory_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_candidate_decision_stored_memory_id", table_name="memory_candidate_decision")
    op.drop_index("ix_memory_evidence_event_id", table_name="memory_evidence")
