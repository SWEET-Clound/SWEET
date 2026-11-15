"""Require both ends of evidence links to belong to the same identity."""

from alembic import op

revision = "20261110_0021"
down_revision = "20261110_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table, policy in (
        ("memory_evidence", "memory_evidence_scope"),
        ("memory_dependency", "memory_dependency_scope"),
    ):
        op.execute(
            f"""
            ALTER POLICY {policy} ON {table}
            WITH CHECK (EXISTS (
                SELECT 1 FROM memory_item m
                JOIN memory_event e ON e.id={table}.event_id
                  AND e.tenant_id=m.tenant_id AND e.user_id=m.user_id AND e.agent_id=m.agent_id
                WHERE m.id={table}.memory_id
                  AND m.tenant_id=current_setting('app.tenant_id', true)
                  AND m.user_id=current_setting('app.user_id', true)
                  AND m.agent_id=current_setting('app.agent_id', true)
            ))
            """
        )


def downgrade() -> None:
    for table, policy in (
        ("memory_evidence", "memory_evidence_scope"),
        ("memory_dependency", "memory_dependency_scope"),
    ):
        op.execute(
            f"""
            ALTER POLICY {policy} ON {table}
            WITH CHECK (EXISTS (
                SELECT 1 FROM memory_item m
                WHERE m.id={table}.memory_id
                  AND m.tenant_id=current_setting('app.tenant_id', true)
                  AND m.user_id=current_setting('app.user_id', true)
                  AND m.agent_id=current_setting('app.agent_id', true)
            ))
            """
        )
