"""Add scoped memory relations for unresolved conflicts.

Revision ID: 20261104_0005
Revises: 20261104_0004
"""

from alembic import op

revision = "20261104_0005"
down_revision = "20261104_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE memory_relation (
            tenant_id text NOT NULL,
            user_id text NOT NULL,
            agent_id text NOT NULL,
            from_memory_id uuid NOT NULL REFERENCES memory_item(id) ON DELETE CASCADE,
            to_memory_id uuid NOT NULL REFERENCES memory_item(id) ON DELETE CASCADE,
            relation text NOT NULL CHECK (
                relation IN ('supports','contradicts','related_to','derived_from','supersedes')
            ),
            confidence double precision NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (from_memory_id, to_memory_id, relation),
            CHECK (from_memory_id <> to_memory_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_memory_relation_to ON memory_relation (to_memory_id, relation)"
    )
    op.execute("ALTER TABLE memory_relation ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY memory_relation_scope ON memory_relation
        USING (
            tenant_id=current_setting('app.tenant_id', true)
            AND user_id=current_setting('app.user_id', true)
            AND agent_id=current_setting('app.agent_id', true)
        )
        WITH CHECK (
            tenant_id=current_setting('app.tenant_id', true)
            AND user_id=current_setting('app.user_id', true)
            AND agent_id=current_setting('app.agent_id', true)
            AND EXISTS (
                SELECT 1 FROM memory_item m
                WHERE m.id=from_memory_id
                  AND m.tenant_id=memory_relation.tenant_id
                  AND m.user_id=memory_relation.user_id
                  AND m.agent_id=memory_relation.agent_id
            )
            AND EXISTS (
                SELECT 1 FROM memory_item m
                WHERE m.id=to_memory_id
                  AND m.tenant_id=memory_relation.tenant_id
                  AND m.user_id=memory_relation.user_id
                  AND m.agent_id=memory_relation.agent_id
            )
        )
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='memory_app') THEN
                GRANT SELECT, INSERT ON memory_relation TO memory_app;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='memory_worker') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON memory_relation TO memory_worker;
            END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE memory_relation")
