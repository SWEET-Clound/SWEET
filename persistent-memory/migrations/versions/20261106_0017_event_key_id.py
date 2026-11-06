"""Track which event key encrypted each newly written payload."""

from alembic import op

revision = "20261106_0017"
down_revision = "20261106_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE memory_event ADD COLUMN ciphertext_key_id text "
        "CHECK (ciphertext_key_id IS NULL OR ciphertext IS NOT NULL)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE memory_event DROP COLUMN ciphertext_key_id")
