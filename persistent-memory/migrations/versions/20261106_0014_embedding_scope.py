"""Apply embedding eligibility at the API role's database boundary."""

from alembic import op

revision = "20261106_0014"
down_revision = "20261106_0013"
branch_labels = None
depends_on = None

_POLICY = """
    ALTER POLICY memory_embedding_scope ON memory_embedding
    USING (EXISTS (
        SELECT 1 FROM memory_item m
        WHERE m.id=memory_embedding.memory_id
          AND m.tenant_id=current_setting('app.tenant_id', true)
          AND m.user_id=current_setting('app.user_id', true)
          AND m.agent_id=current_setting('app.agent_id', true)
          AND m.status='active'
          AND m.sensitivity IN ('low','medium')
          {eligibility}
    ))
"""


def upgrade() -> None:
    op.execute(_POLICY.format(eligibility="""
        AND m.layer IN ('L2','L3')
        AND cardinality(m.poisoning_flags)=0
    """))


def downgrade() -> None:
    op.execute(_POLICY.format(eligibility=""))
