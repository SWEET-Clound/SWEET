"""Distinguish archived episodic memories from expired state.

Revision ID: 20261104_0007
Revises: 20261104_0006
"""

from alembic import op

revision = "20261104_0007"
down_revision = "20261104_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE memory_item DROP CONSTRAINT memory_item_status_check")
    op.execute(
        """
        ALTER TABLE memory_item ADD CONSTRAINT memory_item_status_check
        CHECK (status IN (
            'active','superseded','deleting','quarantined','rejected','expired','archived'
        ))
        """
    )


def downgrade() -> None:
    op.execute("UPDATE memory_item SET status='expired' WHERE status='archived'")
    op.execute("ALTER TABLE memory_item DROP CONSTRAINT memory_item_status_check")
    op.execute(
        """
        ALTER TABLE memory_item ADD CONSTRAINT memory_item_status_check
        CHECK (status IN (
            'active','superseded','deleting','quarantined','rejected','expired'
        ))
        """
    )
