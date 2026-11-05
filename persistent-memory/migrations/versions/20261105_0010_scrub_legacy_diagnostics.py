"""Scrub stale recall references and rejected candidate metadata."""

from alembic import op

revision = "20261105_0010"
down_revision = "20261105_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE memory_candidate_decision
        SET kind='redacted', subject_key=NULL, poisoning_flags=ARRAY[]::text[]
        WHERE outcome='rejected'
          AND (kind<>'redacted' OR subject_key IS NOT NULL OR cardinality(poisoning_flags)>0)
        """
    )
    op.execute(
        """
        WITH cleaned AS (
            SELECT d.id, ARRAY(
                SELECT selected.id
                FROM unnest(d.selected_memory_ids) WITH ORDINALITY AS selected(id, rank)
                JOIN memory_item m ON m.id=selected.id
                WHERE m.tenant_id=d.tenant_id AND m.user_id=d.user_id
                  AND m.agent_id=d.agent_id AND m.status<>'deleting'
                ORDER BY selected.rank
            ) AS ids
            FROM memory_recall_diagnostic d
            WHERE cardinality(d.selected_memory_ids)>0
        )
        UPDATE memory_recall_diagnostic d SET selected_memory_ids=cleaned.ids
        FROM cleaned
        WHERE d.id=cleaned.id AND d.selected_memory_ids IS DISTINCT FROM cleaned.ids
        """
    )


def downgrade() -> None:
    # Data-only migration: scrubbed values cannot be reconstructed.
    pass
