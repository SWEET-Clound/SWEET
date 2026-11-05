"""Harden deletion permissions and version-chain cleanup.

Revision ID: 20261104_0002
Revises: 20261029_0001
"""

from alembic import op

revision = "20261104_0002"
down_revision = "20261029_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE memory_item DROP CONSTRAINT memory_item_supersedes_id_fkey"
    )
    op.execute(
        """
        ALTER TABLE memory_item
        ADD CONSTRAINT memory_item_supersedes_id_fkey
        FOREIGN KEY (supersedes_id) REFERENCES memory_item(id) ON DELETE SET NULL
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='memory_app') THEN
                GRANT DELETE ON memory_evidence TO memory_app;
                GRANT UPDATE ON memory_outbox TO memory_app;
            END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE memory_item DROP CONSTRAINT memory_item_supersedes_id_fkey"
    )
    op.execute(
        """
        ALTER TABLE memory_item
        ADD CONSTRAINT memory_item_supersedes_id_fkey
        FOREIGN KEY (supersedes_id) REFERENCES memory_item(id)
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='memory_app') THEN
                REVOKE DELETE ON memory_evidence FROM memory_app;
                REVOKE UPDATE ON memory_outbox FROM memory_app;
            END IF;
        END $$
        """
    )
