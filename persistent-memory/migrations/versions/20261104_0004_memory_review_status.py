"""Add rejected status for reviewed candidates.

Revision ID: 20261104_0004
Revises: 20261104_0003
"""

from alembic import op

revision = "20261104_0004"
down_revision = "20261104_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE memory_item DROP CONSTRAINT memory_item_status_check")
    op.execute(
        """
        ALTER TABLE memory_item ADD CONSTRAINT memory_item_status_check
        CHECK (status IN (
            'active','superseded','deleting','quarantined','rejected','expired'
        ))
        """
    )


def downgrade() -> None:
    op.execute("UPDATE memory_item SET status='expired' WHERE status='rejected'")
    op.execute("ALTER TABLE memory_item DROP CONSTRAINT memory_item_status_check")
    op.execute(
        """
        ALTER TABLE memory_item ADD CONSTRAINT memory_item_status_check
        CHECK (status IN ('active','superseded','deleting','quarantined','expired'))
        """
    )
