"""Initial persistent memory schema.

Revision ID: 20261029_0001
Revises:
"""

from alembic import op

revision = "20261029_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        """
        CREATE TABLE memory_event (
            id uuid PRIMARY KEY,
            tenant_id text NOT NULL,
            user_id text NOT NULL,
            agent_id text NOT NULL,
            thread_id text NOT NULL,
            idempotency_key text NOT NULL,
            payload_hash text NOT NULL,
            source text NOT NULL CHECK (source IN ('user','assistant','tool','web','admin')),
            content text NOT NULL,
            occurred_at timestamptz NOT NULL,
            recorded_at timestamptz NOT NULL DEFAULT now(),
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            prohibited_from_memory boolean NOT NULL DEFAULT false,
            redacted_at timestamptz,
            UNIQUE (tenant_id, user_id, agent_id, idempotency_key)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE memory_item (
            id uuid PRIMARY KEY,
            tenant_id text NOT NULL,
            user_id text NOT NULL,
            agent_id text NOT NULL,
            statement text NOT NULL,
            layer text NOT NULL CHECK (layer IN ('L1','L2','L3','L4')),
            kind text NOT NULL,
            status text NOT NULL CHECK (status IN ('active','superseded','deleting','quarantined','expired')),
            confidence double precision NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
            policy_version text NOT NULL,
            valid_from timestamptz,
            valid_to timestamptz,
            expires_at timestamptz,
            supersedes_id uuid REFERENCES memory_item(id),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            deleted_at timestamptz,
            fingerprint text,
            future_utility text CHECK (future_utility IN ('low','medium','high')),
            durability text CHECK (durability IN ('transient','uncertain','stable')),
            affect jsonb,
            search_vector tsvector GENERATED ALWAYS AS (
                to_tsvector('simple', coalesce(statement, ''))
            ) STORED
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_memory_item_fingerprint
        ON memory_item (tenant_id, user_id, agent_id, fingerprint)
        WHERE fingerprint IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE TABLE memory_evidence (
            memory_id uuid NOT NULL REFERENCES memory_item(id) ON DELETE CASCADE,
            event_id uuid NOT NULL REFERENCES memory_event(id) ON DELETE CASCADE,
            PRIMARY KEY (memory_id, event_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE memory_outbox (
            id bigserial PRIMARY KEY,
            event_id uuid NOT NULL REFERENCES memory_event(id) ON DELETE CASCADE,
            tenant_id text NOT NULL,
            user_id text NOT NULL,
            agent_id text NOT NULL,
            kind text NOT NULL CHECK (kind IN ('extract')),
            status text NOT NULL CHECK (status IN ('pending','processing','retry','completed','failed')),
            attempts integer NOT NULL DEFAULT 0,
            available_at timestamptz NOT NULL DEFAULT now(),
            locked_at timestamptz,
            completed_at timestamptz,
            error_code text,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE deletion_job (
            id uuid PRIMARY KEY,
            tenant_id text NOT NULL,
            user_id text NOT NULL,
            agent_id text NOT NULL,
            memory_ids uuid[] NOT NULL DEFAULT '{}',
            event_ids uuid[] NOT NULL DEFAULT '{}',
            status text NOT NULL CHECK (
                status IN ('pending','processing','retry','completed','failed')
            ),
            attempts integer NOT NULL DEFAULT 0,
            available_at timestamptz NOT NULL DEFAULT now(),
            locked_at timestamptz,
            error_code text,
            created_at timestamptz NOT NULL DEFAULT now(),
            completed_at timestamptz
        )
        """
    )
    op.execute(
        """
        CREATE TABLE command_dedup (
            tenant_id text NOT NULL,
            user_id text NOT NULL,
            agent_id text NOT NULL,
            idempotency_key text NOT NULL,
            payload_hash text NOT NULL,
            status text NOT NULL CHECK (status IN ('processing','completed')),
            receipt jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            completed_at timestamptz,
            PRIMARY KEY (tenant_id, user_id, agent_id, idempotency_key)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE memory_audit (
            id uuid PRIMARY KEY,
            tenant_id text NOT NULL,
            user_id text NOT NULL,
            agent_id text NOT NULL,
            action text NOT NULL,
            target_id text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_memory_event_scope_time ON memory_event (tenant_id, user_id, agent_id, occurred_at DESC)")
    op.execute("CREATE INDEX ix_memory_item_scope_status ON memory_item (tenant_id, user_id, agent_id, status, updated_at DESC)")
    op.execute("CREATE INDEX ix_memory_item_search ON memory_item USING gin (search_vector)")
    op.execute("CREATE INDEX ix_memory_item_trigram ON memory_item USING gin (statement gin_trgm_ops)")
    op.execute("CREATE INDEX ix_memory_outbox_ready ON memory_outbox (available_at, id) WHERE status IN ('pending','retry')")
    op.execute(
        "CREATE INDEX ix_deletion_job_ready ON deletion_job (available_at, created_at) "
        "WHERE status IN ('pending','retry')"
    )

    for table in (
        "memory_event",
        "memory_item",
        "memory_outbox",
        "deletion_job",
        "command_dedup",
        "memory_audit",
    ):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_scope ON {table}
            USING (
                tenant_id = current_setting('app.tenant_id', true)
                AND user_id = current_setting('app.user_id', true)
                AND agent_id = current_setting('app.agent_id', true)
            )
            WITH CHECK (
                tenant_id = current_setting('app.tenant_id', true)
                AND user_id = current_setting('app.user_id', true)
                AND agent_id = current_setting('app.agent_id', true)
            )
            """
        )
    op.execute("ALTER TABLE memory_evidence ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY memory_evidence_scope ON memory_evidence
        USING (
            EXISTS (
                SELECT 1 FROM memory_item m
                WHERE m.id=memory_id
                  AND m.tenant_id=current_setting('app.tenant_id', true)
                  AND m.user_id=current_setting('app.user_id', true)
                  AND m.agent_id=current_setting('app.agent_id', true)
            )
        )
        WITH CHECK (
            EXISTS (
                SELECT 1 FROM memory_item m
                WHERE m.id=memory_id
                  AND m.tenant_id=current_setting('app.tenant_id', true)
                  AND m.user_id=current_setting('app.user_id', true)
                  AND m.agent_id=current_setting('app.agent_id', true)
            )
        )
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='memory_app') THEN
                GRANT USAGE ON SCHEMA public TO memory_app;
                GRANT SELECT, INSERT, UPDATE ON
                    memory_event, memory_item, memory_evidence, deletion_job,
                    command_dedup, memory_audit
                TO memory_app;
                GRANT SELECT, INSERT ON memory_outbox TO memory_app;
                GRANT DELETE ON command_dedup TO memory_app;
                GRANT USAGE ON SEQUENCE memory_outbox_id_seq TO memory_app;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='memory_worker') THEN
                GRANT USAGE ON SCHEMA public TO memory_worker;
                GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public
                TO memory_worker;
                GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO memory_worker;
            END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS memory_audit")
    op.execute("DROP TABLE IF EXISTS command_dedup")
    op.execute("DROP TABLE IF EXISTS deletion_job")
    op.execute("DROP TABLE IF EXISTS memory_outbox")
    op.execute("DROP TABLE IF EXISTS memory_evidence")
    op.execute("DROP TABLE IF EXISTS memory_item")
    op.execute("DROP TABLE IF EXISTS memory_event")

