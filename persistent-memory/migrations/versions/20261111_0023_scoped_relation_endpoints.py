"""Enforce scoped relation endpoints even for roles that bypass RLS."""

from alembic import op

revision = "20261111_0023"
down_revision = "20261110_0022"
branch_labels = None
depends_on = None

SCOPE_COLUMNS = ["tenant_id", "user_id", "agent_id"]


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_memory_item_id_scope", "memory_item", ["id", *SCOPE_COLUMNS]
    )
    for endpoint in ("from", "to"):
        op.create_foreign_key(
            f"fk_memory_relation_{endpoint}_scope",
            "memory_relation",
            "memory_item",
            [f"{endpoint}_memory_id", *SCOPE_COLUMNS],
            ["id", *SCOPE_COLUMNS],
            ondelete="CASCADE",
        )
        op.drop_constraint(
            f"memory_relation_{endpoint}_memory_id_fkey", "memory_relation", type_="foreignkey"
        )


def downgrade() -> None:
    for endpoint in ("from", "to"):
        op.create_foreign_key(
            f"memory_relation_{endpoint}_memory_id_fkey",
            "memory_relation",
            "memory_item",
            [f"{endpoint}_memory_id"],
            ["id"],
            ondelete="CASCADE",
        )
        op.drop_constraint(
            f"fk_memory_relation_{endpoint}_scope", "memory_relation", type_="foreignkey"
        )
    op.drop_constraint("uq_memory_item_id_scope", "memory_item", type_="unique")
