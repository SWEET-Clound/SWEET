"""Persist a retry delay for ciphertext that cannot be rotated."""

from alembic import op

revision = "20261106_0018"
down_revision = "20261106_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE memory_event ADD COLUMN ciphertext_retry_at timestamptz")


def downgrade() -> None:
    op.execute("ALTER TABLE memory_event DROP COLUMN ciphertext_retry_at")
