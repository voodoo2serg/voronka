"""VR-454: project-scoped Connection Vault (Fernet-sealed secrets)."""
from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "crm_connection_vault" in inspector.get_table_names():
        return
    op.create_table(
        "crm_connection_vault",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False, index=True),
        sa.Column("branch_id", sa.String(36), sa.ForeignKey("crm_branches.id"), nullable=True, index=True),
        sa.Column("type", sa.String(40), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.Column("secrets_blob", sa.Text(), nullable=True),
        sa.Column("secret_masks", sa.JSON(), nullable=False),
        sa.Column("has_secret", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("legacy_connection_id", sa.String(36), sa.ForeignKey("connections.id"), nullable=True),
        sa.Column("legacy_source", sa.String(40), nullable=True),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("admins.telegram_id"), nullable=True),
        sa.Column("rotated_by", sa.BigInteger(), sa.ForeignKey("admins.telegram_id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "type IN ('telegram_bot', 'telegram_channel', 'max_bot', 'email_smtp', 'robokassa', 'yookassa')",
            name="ck_crm_connection_vault_type",
        ),
    )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "crm_connection_vault" in inspector.get_table_names():
        op.drop_table("crm_connection_vault")
