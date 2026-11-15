"""Track required sources of user-confirmed multi-event summaries."""

from alembic import op

revision = "20261110_0019"
down_revision = "20261106_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE memory_dependency (
            memory_id uuid NOT NULL REFERENCES memory_item(id) ON DELETE CASCADE,
            event_id uuid NOT NULL REFERENCES memory_event(id) ON DELETE CASCADE,
            PRIMARY KEY (memory_id, event_id)
        )
        """
    )
    op.execute("CREATE INDEX memory_dependency_event_idx ON memory_dependency (event_id)")
    op.execute("ALTER TABLE memory_dependency ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY memory_dependency_scope ON memory_dependency
        USING (EXISTS (
            SELECT 1 FROM memory_item m
            WHERE m.id=memory_dependency.memory_id
              AND m.tenant_id=current_setting('app.tenant_id', true)
              AND m.user_id=current_setting('app.user_id', true)
              AND m.agent_id=current_setting('app.agent_id', true)
        ))
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='memory_app') THEN
                GRANT SELECT, INSERT ON memory_dependency TO memory_app;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='memory_worker') THEN
                GRANT SELECT ON memory_dependency TO memory_worker;
            END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            LOCK TABLE memory_dependency IN ACCESS EXCLUSIVE MODE;
            IF EXISTS (SELECT 1 FROM memory_dependency) THEN
                RAISE EXCEPTION 'cannot discard required summary sources';
            END IF;
        END $$
        """
    )
    op.execute("DROP TABLE memory_dependency")
