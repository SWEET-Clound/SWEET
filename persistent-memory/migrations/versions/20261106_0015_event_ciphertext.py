"""Allow raw event bodies to be stored as authenticated ciphertext."""

from alembic import op

revision = "20261106_0015"
down_revision = "20261106_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE memory_event ADD COLUMN ciphertext bytea")
    op.execute(
        "ALTER TABLE memory_event ADD CONSTRAINT memory_event_ciphertext_exclusive "
        "CHECK (ciphertext IS NULL OR (content='' AND metadata='{}'::jsonb))"
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            LOCK TABLE memory_event IN ACCESS EXCLUSIVE MODE;
            IF EXISTS (SELECT 1 FROM memory_event WHERE ciphertext IS NOT NULL) THEN
                RAISE EXCEPTION 'cannot downgrade while encrypted event bodies exist';
            END IF;
        END $$
        """
    )
    op.execute("ALTER TABLE memory_event DROP COLUMN ciphertext")
