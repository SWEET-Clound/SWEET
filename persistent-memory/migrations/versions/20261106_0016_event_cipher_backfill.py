"""Index the shrinking set of raw events awaiting encryption."""

from alembic import op

revision = "20261106_0016"
down_revision = "20261106_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX memory_event_cipher_backfill_idx ON memory_event (recorded_at, id) "
        "WHERE ciphertext IS NULL AND redacted_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX memory_event_cipher_backfill_idx")
