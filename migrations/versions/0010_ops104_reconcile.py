"""OPS-104: correlation_id on commerce outbox/inbox/entitlement + dead index."""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    outbox_cols = {c["name"] for c in inspector.get_columns("crm_integration_outbox")}
    if "correlation_id" not in outbox_cols:
        op.add_column(
            "crm_integration_outbox",
            sa.Column("correlation_id", sa.String(length=80), nullable=True),
        )
        op.create_index(
            "ix_crm_integration_outbox_correlation_id",
            "crm_integration_outbox",
            ["correlation_id"],
        )

    inbox_cols = {c["name"] for c in inspector.get_columns("crm_integration_inbox")}
    if "correlation_id" not in inbox_cols:
        op.add_column(
            "crm_integration_inbox",
            sa.Column("correlation_id", sa.String(length=80), nullable=True),
        )

    job_cols = {c["name"] for c in inspector.get_columns("crm_entitlement_jobs")}
    if "correlation_id" not in job_cols:
        op.add_column(
            "crm_entitlement_jobs",
            sa.Column("correlation_id", sa.String(length=80), nullable=True),
        )

    # Partial-friendly helper for DLQ listing (state filter already indexed with next_attempt_at).
    indexes = {idx["name"] for idx in inspector.get_indexes("crm_integration_outbox")}
    if "ix_crm_integration_outbox_state_only" not in indexes:
        op.create_index(
            "ix_crm_integration_outbox_state_only",
            "crm_integration_outbox",
            ["state"],
        )


def downgrade():
    op.drop_index("ix_crm_integration_outbox_state_only", table_name="crm_integration_outbox")
    op.drop_index("ix_crm_integration_outbox_correlation_id", table_name="crm_integration_outbox")
    op.drop_column("crm_integration_outbox", "correlation_id")
    op.drop_column("crm_integration_inbox", "correlation_id")
    op.drop_column("crm_entitlement_jobs", "correlation_id")
