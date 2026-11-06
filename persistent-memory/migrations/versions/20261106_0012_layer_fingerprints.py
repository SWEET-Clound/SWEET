"""Separate deduplication namespaces by memory layer."""

from alembic import op

revision = "20261106_0012"
down_revision = "20261105_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX uq_memory_item_fingerprint")
    op.execute(
        "CREATE UNIQUE INDEX uq_memory_item_fingerprint "
        "ON memory_item (tenant_id, user_id, agent_id, layer, fingerprint) "
        "WHERE fingerprint IS NOT NULL"
    )


def downgrade() -> None:
    # PostgreSQL rejects the downgrade if cross-layer duplicates exist.
    op.execute("DROP INDEX uq_memory_item_fingerprint")
    op.execute(
        "CREATE UNIQUE INDEX uq_memory_item_fingerprint "
        "ON memory_item (tenant_id, user_id, agent_id, fingerprint) "
        "WHERE fingerprint IS NOT NULL"
    )
