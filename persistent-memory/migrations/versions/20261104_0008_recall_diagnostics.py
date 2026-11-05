"""Persist privacy-safe recall diagnostics.

Revision ID: 20261104_0008
Revises: 20261104_0007
"""

from alembic import op

revision = "20261104_0008"
down_revision = "20261104_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE memory_recall_diagnostic (
            id uuid PRIMARY KEY,
            tenant_id text NOT NULL,
            user_id text NOT NULL,
            agent_id text NOT NULL,
            purpose text NOT NULL CHECK (purpose IN ('conversation','task','history','support')),
            token_budget integer NOT NULL CHECK (token_budget >= 0),
            candidate_count integer NOT NULL CHECK (candidate_count >= 0),
            selected_memory_ids uuid[] NOT NULL DEFAULT '{}',
            truncated boolean NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_recall_diagnostic_scope_time ON memory_recall_diagnostic "
        "(tenant_id, user_id, agent_id, created_at DESC)"
    )
    op.execute("ALTER TABLE memory_recall_diagnostic ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY memory_recall_diagnostic_scope ON memory_recall_diagnostic
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
                GRANT SELECT, INSERT ON memory_recall_diagnostic TO memory_app;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='memory_worker') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON memory_recall_diagnostic
                TO memory_worker;
            END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE memory_recall_diagnostic")
