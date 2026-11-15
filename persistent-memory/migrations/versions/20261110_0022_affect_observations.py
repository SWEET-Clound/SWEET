"""Keep event-bound affect observations without overwriting earlier observations."""

from alembic import op

revision = "20261110_0022"
down_revision = "20261110_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE memory_affect_observation (
            memory_id uuid NOT NULL REFERENCES memory_item(id) ON DELETE CASCADE,
            event_id uuid NOT NULL REFERENCES memory_event(id) ON DELETE CASCADE,
            affect jsonb NOT NULL CHECK (jsonb_typeof(affect)='object'),
            observed_at timestamptz NOT NULL,
            expires_at timestamptz,
            recorded_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (memory_id, event_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX memory_affect_history_idx ON memory_affect_observation "
        "(memory_id, observed_at DESC, event_id)"
    )
    op.execute("CREATE INDEX memory_affect_event_idx ON memory_affect_observation (event_id)")
    op.execute("ALTER TABLE memory_affect_observation ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY memory_affect_scope ON memory_affect_observation
        USING (EXISTS (
            SELECT 1 FROM memory_item m
            JOIN memory_event e ON e.id=memory_affect_observation.event_id
              AND e.tenant_id=m.tenant_id AND e.user_id=m.user_id AND e.agent_id=m.agent_id
            WHERE m.id=memory_affect_observation.memory_id
              AND m.tenant_id=current_setting('app.tenant_id', true)
              AND m.user_id=current_setting('app.user_id', true)
              AND m.agent_id=current_setting('app.agent_id', true)
              AND m.status<>'deleting' AND e.prohibited_from_memory=false
        ))
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='memory_app') THEN
                GRANT SELECT, INSERT ON memory_affect_observation TO memory_app;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='memory_worker') THEN
                GRANT SELECT, INSERT, DELETE ON memory_affect_observation TO memory_worker;
            END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            LOCK TABLE memory_affect_observation IN ACCESS EXCLUSIVE MODE;
            IF EXISTS (SELECT 1 FROM memory_affect_observation) THEN
                RAISE EXCEPTION 'cannot discard affect observation history';
            END IF;
        END $$
        """
    )
    op.execute("DROP TABLE memory_affect_observation")
