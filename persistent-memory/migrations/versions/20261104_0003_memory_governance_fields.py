"""Persist memory governance fields.

Revision ID: 20261104_0003
Revises: 20261104_0002
"""

from alembic import op

revision = "20261104_0003"
down_revision = "20261104_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE memory_item ADD COLUMN subject_key text")
    op.execute(
        """
        ALTER TABLE memory_item ADD COLUMN sensitivity text NOT NULL DEFAULT 'low'
        CHECK (sensitivity IN ('low','medium','high','secret'))
        """
    )
    op.execute(
        "ALTER TABLE memory_item ADD COLUMN poisoning_flags text[] NOT NULL DEFAULT '{}'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE memory_item DROP COLUMN poisoning_flags")
    op.execute("ALTER TABLE memory_item DROP COLUMN sensitivity")
    op.execute("ALTER TABLE memory_item DROP COLUMN subject_key")
