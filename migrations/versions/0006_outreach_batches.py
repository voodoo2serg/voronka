"""Warm-up outreach batches, separate from Campaign/Outbox."""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"


def upgrade():
    op.create_table(
        "crm_outreach_batches",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("project_id", sa.String(length=36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("invite_text", sa.Text(), nullable=False),
        sa.Column("entry_token", sa.String(length=40), sa.ForeignKey("entries.token"), nullable=False),
        sa.Column("entry_connection_id", sa.String(length=36), sa.ForeignKey("connections.id"), nullable=False),
        sa.Column("daily_cap", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("transport", sa.String(length=20), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), sa.ForeignKey("admins.telegram_id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("daily_cap >= 1 AND daily_cap <= 50"),
        sa.CheckConstraint("mode IN ('dry_run', 'live')"),
        sa.CheckConstraint("transport IN ('manual', 'pymax')"),
        sa.CheckConstraint("state IN ('draft', 'prepared', 'paused', 'cancelled', 'completed')"),
    )
    op.create_index("ix_crm_outreach_batches_project_id", "crm_outreach_batches", ["project_id"])
    op.create_table(
        "crm_outreach_recipients",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("batch_id", sa.String(length=36), sa.ForeignKey("crm_outreach_batches.id"), nullable=False),
        sa.Column("profile_id", sa.String(length=36), sa.ForeignKey("crm_profiles.id"), nullable=False),
        sa.Column("phone", sa.String(length=80), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("invite_text", sa.Text(), nullable=False),
        sa.Column("bot_link", sa.String(length=500), nullable=False),
        sa.Column("entry_token", sa.String(length=40), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(length=200), nullable=False),
        sa.Column("outcome_note", sa.Text(), nullable=False),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("batch_id", "profile_id"),
        sa.CheckConstraint(
            "state IN ('pending', 'prepared', 'invited', 'started', 'skipped', 'failed', 'stopped')"
        ),
    )
    op.create_index("ix_crm_outreach_recipients_batch_id", "crm_outreach_recipients", ["batch_id"])
    op.create_index("ix_crm_outreach_recipients_profile_id", "crm_outreach_recipients", ["profile_id"])


def downgrade():
    op.drop_index("ix_crm_outreach_recipients_profile_id", table_name="crm_outreach_recipients")
    op.drop_index("ix_crm_outreach_recipients_batch_id", table_name="crm_outreach_recipients")
    op.drop_table("crm_outreach_recipients")
    op.drop_index("ix_crm_outreach_batches_project_id", table_name="crm_outreach_batches")
    op.drop_table("crm_outreach_batches")
