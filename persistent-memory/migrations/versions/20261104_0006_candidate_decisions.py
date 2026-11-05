"""Persist privacy-safe candidate decisions.

Revision ID: 20261104_0006
Revises: 20261104_0005
"""

from alembic import op

revision = "20261104_0006"
down_revision = "20261104_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE memory_candidate_decision (
            id uuid PRIMARY KEY,
            tenant_id text NOT NULL,
            user_id text NOT NULL,
            agent_id text NOT NULL,
            event_id uuid NOT NULL REFERENCES memory_event(id) ON DELETE CASCADE,
            candidate_ordinal integer NOT NULL CHECK (candidate_ordinal >= 0),
            kind text NOT NULL,
            proposed_layer text NOT NULL CHECK (proposed_layer IN ('L1','L2','L3','L4')),
            subject_key text,
            sensitivity text NOT NULL CHECK (sensitivity IN ('low','medium','high','secret')),
            poisoning_flags text[] NOT NULL DEFAULT '{}',
            outcome text NOT NULL CHECK (outcome IN ('stored','quarantined','rejected')),
            reason text NOT NULL,
            policy_version text NOT NULL,
            stored_memory_id uuid REFERENCES memory_item(id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (event_id, candidate_ordinal)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_candidate_decision_scope_time ON memory_candidate_decision "
        "(tenant_id, user_id, agent_id, created_at DESC)"
    )
    op.execute("ALTER TABLE memory_candidate_decision ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY memory_candidate_decision_scope ON memory_candidate_decision
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
                SELECT 1 FROM memory_event e
                WHERE e.id=event_id
                  AND e.tenant_id=memory_candidate_decision.tenant_id
                  AND e.user_id=memory_candidate_decision.user_id
                  AND e.agent_id=memory_candidate_decision.agent_id
            )
            AND (
                stored_memory_id IS NULL OR EXISTS (
                    SELECT 1 FROM memory_item m
                    WHERE m.id=stored_memory_id
                      AND m.tenant_id=memory_candidate_decision.tenant_id
                      AND m.user_id=memory_candidate_decision.user_id
                      AND m.agent_id=memory_candidate_decision.agent_id
                )
            )
        )
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='memory_app') THEN
                GRANT SELECT, INSERT ON memory_candidate_decision TO memory_app;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='memory_worker') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON memory_candidate_decision
                TO memory_worker;
            END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE memory_candidate_decision")
