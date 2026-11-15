"""Wave C: Person/ContactPoint/Consent, interactions, journeys, agreements, ledger, OIDC links.

Additive only — no drops; existing CRM UI tables unchanged except nullable FKs.
Tickets: CRM-001/005/002/003/006/007/008, PAY-002, AUTH-004.
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    op.create_table(
        "crm_persons",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("lifecycle_stage", sa.String(length=40), nullable=False, server_default="active"),
        sa.Column("display_name", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("merged_into_id", sa.String(length=36), sa.ForeignKey("crm_persons.id"), nullable=True),
        sa.Column("extra", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "crm_person_links",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("person_id", sa.String(length=36), sa.ForeignKey("crm_persons.id"), nullable=False),
        sa.Column("service", sa.String(length=40), nullable=False),
        sa.Column("native_id", sa.String(length=160), nullable=False),
        sa.Column("verified_by", sa.String(length=40), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("service", "native_id"),
    )
    op.create_index("ix_crm_person_links_person_id", "crm_person_links", ["person_id"])

    op.create_table(
        "crm_merge_reviews",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("source_person_id", sa.String(length=36), sa.ForeignKey("crm_persons.id"), nullable=False),
        sa.Column("target_person_id", sa.String(length=36), sa.ForeignKey("crm_persons.id"), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False, server_default="pending"),
        sa.Column("reason", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), sa.ForeignKey("admins.telegram_id"), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("state IN ('pending', 'applied', 'rejected', 'undone')"),
    )

    op.create_table(
        "crm_contact_points",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("person_id", sa.String(length=36), sa.ForeignKey("crm_persons.id"), nullable=False),
        sa.Column("channel", sa.String(length=30), nullable=False),
        sa.Column("raw_value", sa.String(length=250), nullable=False, server_default=""),
        sa.Column("normalized_value", sa.String(length=250), nullable=False),
        sa.Column("address_hash", sa.String(length=64), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("reachable", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("connection_id", sa.String(length=36), sa.ForeignKey("connections.id"), nullable=True),
        sa.Column("identity_id", sa.String(length=36), sa.ForeignKey("identities.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("person_id", "channel", "address_hash"),
    )
    op.create_index("ix_crm_contact_points_address_hash", "crm_contact_points", ["address_hash"])
    op.create_index("ix_crm_contact_points_person_id", "crm_contact_points", ["person_id"])

    op.create_table(
        "crm_contact_verifications",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("contact_point_id", sa.String(length=36), sa.ForeignKey("crm_contact_points.id"), nullable=False),
        sa.Column("method", sa.String(length=40), nullable=False),
        sa.Column("outcome", sa.String(length=30), nullable=False),
        sa.Column("evidence", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("actor_id", sa.BigInteger(), sa.ForeignKey("admins.telegram_id"), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("outcome IN ('verified', 'rejected', 'expired')"),
    )
    op.create_index("ix_crm_contact_verifications_cp", "crm_contact_verifications", ["contact_point_id"])

    op.create_table(
        "crm_consents",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("contact_point_id", sa.String(length=36), sa.ForeignKey("crm_contact_points.id"), nullable=False),
        sa.Column("purpose", sa.String(length=40), nullable=False),
        sa.Column("lawful_basis", sa.String(length=40), nullable=False, server_default="consent"),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("evidence", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("contact_point_id", "purpose"),
        sa.CheckConstraint("status IN ('granted', 'denied', 'revoked', 'unknown')"),
        sa.CheckConstraint(
            "purpose IN ('marketing', 'transactional', 'service', 'analytics')"
        ),
    )
    op.create_index("ix_crm_consents_cp", "crm_consents", ["contact_point_id"])

    op.create_table(
        "crm_interactions",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("person_id", sa.String(length=36), sa.ForeignKey("crm_persons.id"), nullable=False),
        sa.Column("project_id", sa.String(length=36), sa.ForeignKey("projects.id"), nullable=True),
        sa.Column("contact_point_id", sa.String(length=36), sa.ForeignKey("crm_contact_points.id"), nullable=True),
        sa.Column("communication_id", sa.String(length=36), nullable=True),
        sa.Column("kind", sa.String(length=60), nullable=False),
        sa.Column("direction", sa.String(length=20), nullable=False, server_default="inbound"),
        sa.Column("channel", sa.String(length=30), nullable=False, server_default=""),
        sa.Column("evidence_status", sa.String(length=30), nullable=False, server_default="recorded"),
        sa.Column("source_service", sa.String(length=40), nullable=False, server_default="voronka"),
        sa.Column("external_event_key", sa.String(length=160), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_service", "external_event_key"),
        sa.CheckConstraint("direction IN ('inbound', 'outbound', 'system')"),
    )
    op.create_index("ix_crm_interactions_person", "crm_interactions", ["person_id", "occurred_at"])
    op.create_index("ix_crm_interactions_project", "crm_interactions", ["project_id"])

    op.create_table(
        "crm_communications",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("person_id", sa.String(length=36), sa.ForeignKey("crm_persons.id"), nullable=False),
        sa.Column("contact_point_id", sa.String(length=36), sa.ForeignKey("crm_contact_points.id"), nullable=True),
        sa.Column("project_id", sa.String(length=36), sa.ForeignKey("projects.id"), nullable=True),
        sa.Column("campaign_id", sa.String(length=36), sa.ForeignKey("crm_campaigns.id"), nullable=True),
        sa.Column("journey_step_id", sa.String(length=36), nullable=True),
        sa.Column("outbox_id", sa.String(length=36), sa.ForeignKey("outbox.id"), nullable=True),
        sa.Column("direction", sa.String(length=20), nullable=False, server_default="outbound"),
        sa.Column("purpose", sa.String(length=40), nullable=False, server_default="marketing"),
        sa.Column("channel", sa.String(length=30), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="queued"),
        sa.Column("provider_message_id", sa.String(length=160), nullable=True),
        sa.Column("body_preview", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'accepted', 'delivered', 'failed', 'partial', 'cancelled')"
        ),
    )
    op.create_index("ix_crm_communications_person", "crm_communications", ["person_id"])

    op.create_table(
        "crm_delivery_events",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("communication_id", sa.String(length=36), sa.ForeignKey("crm_communications.id"), nullable=False),
        sa.Column("platform_status", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("normalized_status", sa.String(length=30), nullable=False),
        sa.Column("evidence_ref", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("external_event_key", sa.String(length=160), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("communication_id", "external_event_key"),
        sa.CheckConstraint(
            "normalized_status IN ('queued', 'accepted', 'delivered', 'failed', 'read', 'unknown')"
        ),
    )
    op.create_index("ix_crm_delivery_events_comm", "crm_delivery_events", ["communication_id"])

    op.create_table(
        "crm_replies",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("communication_id", sa.String(length=36), sa.ForeignKey("crm_communications.id"), nullable=False),
        sa.Column("provider_message_id", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("kind", sa.String(length=40), nullable=False, server_default="message"),
        sa.Column("body_preview", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("communication_id", "provider_message_id"),
    )

    op.create_table(
        "crm_journeys",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("person_id", sa.String(length=36), sa.ForeignKey("crm_persons.id"), nullable=False),
        sa.Column("project_id", sa.String(length=36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("campaign_id", sa.String(length=36), sa.ForeignKey("crm_campaigns.id"), nullable=True),
        sa.Column("purpose", sa.String(length=40), nullable=False, server_default="warmup"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="active"),
        sa.Column("title", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'completed', 'cancelled', 'opted_out')"
        ),
    )
    op.create_index("ix_crm_journeys_person", "crm_journeys", ["person_id"])
    op.create_index("ix_crm_journeys_project", "crm_journeys", ["project_id"])

    op.create_table(
        "crm_journey_steps",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("journey_id", sa.String(length=36), sa.ForeignKey("crm_journeys.id"), nullable=False),
        sa.Column("step_no", sa.Integer(), nullable=False),
        sa.Column("condition", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="pending"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("communication_id", sa.String(length=36), sa.ForeignKey("crm_communications.id"), nullable=True),
        sa.Column("notes", sa.String(length=500), nullable=False, server_default=""),
        sa.UniqueConstraint("journey_id", "step_no"),
        sa.CheckConstraint(
            "status IN ('pending', 'due', 'sent', 'skipped', 'failed', 'waiting_reply', 'done')"
        ),
    )
    op.create_index("ix_crm_journey_steps_journey", "crm_journey_steps", ["journey_id"])

    # Soft refs communication_id / journey_step_id stay nullable columns without
    # ALTER FK (SQLite cannot add constraints; Postgres create_all/models cover ORM FKs).

    op.create_table(
        "crm_agreements",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("person_id", sa.String(length=36), sa.ForeignKey("crm_persons.id"), nullable=False),
        sa.Column("project_id", sa.String(length=36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("deal_id", sa.String(length=36), sa.ForeignKey("crm_deals.id"), nullable=True),
        sa.Column("legal_status", sa.String(length=30), nullable=False, server_default="draft"),
        sa.Column("title", sa.String(length=250), nullable=False, server_default=""),
        sa.Column("offer_acceptance", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence", sa.String(length=2000), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "legal_status IN ('draft', 'sent', 'accepted', 'signed', 'void', 'expired')"
        ),
    )
    op.create_index("ix_crm_agreements_person", "crm_agreements", ["person_id"])
    op.create_index("ix_crm_agreements_project", "crm_agreements", ["project_id"])

    op.create_table(
        "crm_order_lines",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("order_id", sa.String(length=36), sa.ForeignKey("crm_orders.id"), nullable=False),
        sa.Column("product_id", sa.String(length=36), sa.ForeignKey("crm_products.id"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="RUB"),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.CheckConstraint("amount_minor >= 0"),
        sa.CheckConstraint("quantity >= 1"),
    )
    op.create_index("ix_crm_order_lines_order", "crm_order_lines", ["order_id"])

    op.create_table(
        "crm_receivables",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("order_id", sa.String(length=36), sa.ForeignKey("crm_orders.id"), nullable=False),
        sa.Column("order_line_id", sa.String(length=36), sa.ForeignKey("crm_order_lines.id"), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("due_minor", sa.BigInteger(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state", sa.String(length=30), nullable=False, server_default="open"),
        sa.Column("allocated_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("due_minor >= 0"),
        sa.CheckConstraint("allocated_minor >= 0"),
        sa.CheckConstraint("state IN ('open', 'partial', 'paid', 'cancelled', 'written_off')"),
        sa.UniqueConstraint("order_id", "sequence"),
    )
    op.create_index("ix_crm_receivables_order", "crm_receivables", ["order_id"])

    op.create_table(
        "crm_payment_allocations",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("payment_id", sa.String(length=36), sa.ForeignKey("crm_payments.id"), nullable=False),
        sa.Column("order_id", sa.String(length=36), sa.ForeignKey("crm_orders.id"), nullable=False),
        sa.Column("receivable_id", sa.String(length=36), sa.ForeignKey("crm_receivables.id"), nullable=True),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_minor > 0"),
        sa.CheckConstraint("state IN ('active', 'reversed')"),
    )
    op.create_index("ix_crm_payment_allocations_order", "crm_payment_allocations", ["order_id"])
    op.create_index("ix_crm_payment_allocations_payment", "crm_payment_allocations", ["payment_id"])

    op.create_table(
        "crm_refunds",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("payment_id", sa.String(length=36), sa.ForeignKey("crm_payments.id"), nullable=False),
        sa.Column("refund_payment_id", sa.String(length=36), sa.ForeignKey("crm_payments.id"), nullable=True),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="confirmed"),
        sa.Column("reason", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_minor > 0"),
        sa.CheckConstraint("status IN ('pending', 'confirmed', 'failed', 'cancelled')"),
    )
    op.create_index("ix_crm_refunds_payment", "crm_refunds", ["payment_id"])

    op.create_table(
        "crm_oidc_links",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("issuer", sa.String(length=250), nullable=False),
        sa.Column("subject", sa.String(length=250), nullable=False),
        sa.Column("account_id", sa.String(length=36), sa.ForeignKey("crm_accounts.id"), nullable=True),
        sa.Column("contact_id", sa.String(length=36), sa.ForeignKey("contacts.id"), nullable=True),
        sa.Column("person_id", sa.String(length=36), sa.ForeignKey("crm_persons.id"), nullable=True),
        sa.Column("profile_id", sa.String(length=36), sa.ForeignKey("crm_profiles.id"), nullable=True),
        sa.Column("provisioning_status", sa.String(length=30), nullable=False, server_default="linked"),
        sa.Column("email_hint", sa.String(length=250), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("issuer", "subject"),
        sa.CheckConstraint(
            "provisioning_status IN ('linked', 'pending', 'revoked', 'conflict')"
        ),
    )
    op.create_index("ix_crm_oidc_links_account", "crm_oidc_links", ["account_id"])
    op.create_index("ix_crm_oidc_links_person", "crm_oidc_links", ["person_id"])

    # Additive nullable columns on existing tables
    contact_cols = {c["name"] for c in inspector.get_columns("contacts")}
    if "person_id" not in contact_cols:
        op.add_column(
            "contacts",
            sa.Column("person_id", sa.String(length=36), sa.ForeignKey("crm_persons.id"), nullable=True),
        )
        op.create_index("ix_contacts_person_id", "contacts", ["person_id"])

    profile_cols = {c["name"] for c in inspector.get_columns("crm_profiles")}
    if "person_id" not in profile_cols:
        op.add_column(
            "crm_profiles",
            sa.Column("person_id", sa.String(length=36), sa.ForeignKey("crm_persons.id"), nullable=True),
        )
        op.create_index("ix_crm_profiles_person_id", "crm_profiles", ["person_id"])

    order_cols = {c["name"] for c in inspector.get_columns("crm_orders")}
    if "person_id" not in order_cols:
        op.add_column(
            "crm_orders",
            sa.Column("person_id", sa.String(length=36), sa.ForeignKey("crm_persons.id"), nullable=True),
        )
    if "agreement_id" not in order_cols:
        op.add_column(
            "crm_orders",
            sa.Column("agreement_id", sa.String(length=36), sa.ForeignKey("crm_agreements.id"), nullable=True),
        )

    deal_cols = {c["name"] for c in inspector.get_columns("crm_deals")}
    if "person_id" not in deal_cols:
        op.add_column(
            "crm_deals",
            sa.Column("person_id", sa.String(length=36), sa.ForeignKey("crm_persons.id"), nullable=True),
        )


def downgrade():
    # Additive wave — keep forward-only in production; empty for Alembic completeness.
    pass
