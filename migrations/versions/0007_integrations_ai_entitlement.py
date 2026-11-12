"""Provider events, entitlement jobs, neighbor integration queues, AI rules, campaign media."""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"


def upgrade():
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("crm_campaigns")}
    if "asset_id" not in cols:
        op.add_column("crm_campaigns", sa.Column("asset_id", sa.String(length=36), sa.ForeignKey("assets.id"), nullable=True))

    op.create_table(
        "crm_provider_events",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("event_key", sa.String(length=200), nullable=False),
        sa.Column("order_id", sa.String(length=36), sa.ForeignKey("crm_orders.id"), nullable=True),
        sa.Column("invoice_id", sa.String(length=36), sa.ForeignKey("crm_invoices.id"), nullable=True),
        sa.Column("payload_redacted", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "event_key"),
    )
    op.create_index("ix_crm_provider_events_order_id", "crm_provider_events", ["order_id"])

    op.create_table(
        "crm_entitlement_jobs",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("payment_id", sa.String(length=36), sa.ForeignKey("crm_payments.id"), nullable=False),
        sa.Column("order_id", sa.String(length=36), sa.ForeignKey("crm_orders.id"), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(length=200), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("payment_id"),
    )
    op.create_index("ix_crm_entitlement_jobs_order_id", "crm_entitlement_jobs", ["order_id"])
    op.create_index("ix_crm_entitlement_jobs_state", "crm_entitlement_jobs", ["state"])

    op.create_table(
        "crm_integration_outbox",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("event_id", sa.String(length=80), nullable=False),
        sa.Column("destination", sa.String(length=40), nullable=False),
        sa.Column("kind", sa.String(length=60), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("event_id"),
    )
    op.create_index("ix_crm_integration_outbox_state", "crm_integration_outbox", ["state", "next_attempt_at"])

    op.create_table(
        "crm_integration_inbox",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("event_key", sa.String(length=160), nullable=False),
        sa.Column("kind", sa.String(length=60), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("profile_id", sa.String(length=36), sa.ForeignKey("crm_profiles.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source", "event_key"),
    )

    op.create_table(
        "crm_ai_rules",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("project_id", sa.String(length=36), sa.ForeignKey("projects.id"), nullable=True),
        sa.Column("connection_id", sa.String(length=36), sa.ForeignKey("connections.id"), nullable=True),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("system_rules", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), sa.ForeignKey("admins.telegram_id"), nullable=True),
        sa.UniqueConstraint("scope", "project_id", "connection_id"),
        sa.CheckConstraint("scope IN ('global', 'project', 'connection')"),
    )


def downgrade():
    op.drop_table("crm_ai_rules")
    op.drop_table("crm_integration_inbox")
    op.drop_index("ix_crm_integration_outbox_state", table_name="crm_integration_outbox")
    op.drop_table("crm_integration_outbox")
    op.drop_index("ix_crm_entitlement_jobs_state", table_name="crm_entitlement_jobs")
    op.drop_index("ix_crm_entitlement_jobs_order_id", table_name="crm_entitlement_jobs")
    op.drop_table("crm_entitlement_jobs")
    op.drop_index("ix_crm_provider_events_order_id", table_name="crm_provider_events")
    op.drop_table("crm_provider_events")
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("crm_campaigns")}
    # SQLite cannot drop FK-backed columns cleanly; Postgres CI covers real downgrade.
    if "asset_id" in cols and bind.dialect.name != "sqlite":
        op.drop_column("crm_campaigns", "asset_id")
