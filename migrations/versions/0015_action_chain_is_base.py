"""CRM-R5: ActionChain.is_base — Save sets Project/Branch base chain."""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "crm_action_chains" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("crm_action_chains")}
    if "is_base" not in cols:
        op.add_column(
            "crm_action_chains",
            sa.Column("is_base", sa.Boolean(), server_default=sa.false(), nullable=False),
        )
        op.create_index("ix_crm_action_chains_is_base", "crm_action_chains", ["is_base"])


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "crm_action_chains" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("crm_action_chains")}
    if "is_base" in cols:
        op.drop_index("ix_crm_action_chains_is_base", table_name="crm_action_chains")
        op.drop_column("crm_action_chains", "is_base")
