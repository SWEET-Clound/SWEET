"""Store model-scoped vectors with the memory lifecycle."""

from alembic import op

revision = "20261106_0013"
down_revision = "20261106_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE memory_embedding (
            memory_id uuid NOT NULL REFERENCES memory_item(id) ON DELETE CASCADE,
            model text NOT NULL CHECK (char_length(model) BETWEEN 1 AND 256),
            dimensions integer NOT NULL CHECK (dimensions BETWEEN 1 AND 4096),
            embedding real[] NOT NULL CHECK (
                array_ndims(embedding)=1 AND cardinality(embedding)=dimensions
                AND array_position(embedding, NULL) IS NULL
            ),
            PRIMARY KEY (memory_id, model, dimensions)
        )
        """
    )
    op.execute("ALTER TABLE memory_embedding ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY memory_embedding_scope ON memory_embedding
        USING (EXISTS (
            SELECT 1 FROM memory_item m
            WHERE m.id=memory_embedding.memory_id
              AND m.tenant_id=current_setting('app.tenant_id', true)
              AND m.user_id=current_setting('app.user_id', true)
              AND m.agent_id=current_setting('app.agent_id', true)
              AND m.status='active'
              AND m.sensitivity IN ('low','medium')
        ))
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='memory_app') THEN
                GRANT SELECT ON memory_embedding TO memory_app;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='memory_worker') THEN
                GRANT SELECT, INSERT, UPDATE ON memory_embedding TO memory_worker;
            END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE memory_embedding")
