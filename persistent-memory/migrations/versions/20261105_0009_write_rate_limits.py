"""Add multi-replica write rate-limit buckets.

Revision ID: 20261105_0009
Revises: 20261104_0008
"""

from alembic import op

revision = "20261105_0009"
down_revision = "20261104_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE memory_write_rate_limit (
            tenant_id text NOT NULL,
            user_id text NOT NULL,
            agent_id text NOT NULL,
            operation text NOT NULL CHECK (operation IN ('event','command')),
            window_started_at timestamptz NOT NULL,
            request_count integer NOT NULL CHECK (request_count > 0),
            PRIMARY KEY (
                tenant_id, user_id, agent_id, operation, window_started_at
            )
        )
        """
    )
    op.execute("ALTER TABLE memory_write_rate_limit ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY memory_write_rate_limit_scope ON memory_write_rate_limit
        USING (
            tenant_id=current_setting('app.tenant_id', true)
            AND user_id=current_setting('app.user_id', true)
            AND agent_id=current_setting('app.agent_id', true)
        )
        WITH CHECK (
            tenant_id=current_setting('app.tenant_id', true)
            AND user_id=current_setting('app.user_id', true)
            AND agent_id=current_setting('app.agent_id', true)
        )
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='memory_app') THEN
                GRANT SELECT, INSERT, UPDATE ON memory_write_rate_limit TO memory_app;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='memory_worker') THEN
                GRANT SELECT, DELETE ON memory_write_rate_limit TO memory_worker;
            END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE memory_write_rate_limit")
