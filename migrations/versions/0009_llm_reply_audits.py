"""PDialog P1: llm_reply audit table (Voronka-only, no Phoenix)."""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"


def upgrade():
    op.create_table(
        "llm_reply_audits",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("execution_id", sa.String(length=36), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("node_id", sa.String(length=80), nullable=False),
        sa.Column("run_id", sa.String(length=36), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("identity_id", sa.String(length=36), sa.ForeignKey("identities.id"), nullable=False),
        sa.Column("contact_id", sa.String(length=36), sa.ForeignKey("contacts.id"), nullable=False),
        sa.Column("connection_id", sa.String(length=36), sa.ForeignKey("connections.id"), nullable=False),
        sa.Column("project_id", sa.String(length=36), sa.ForeignKey("projects.id"), nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("rules_version", sa.String(length=120), nullable=False),
        sa.Column("mode", sa.String(length=30), nullable=False),
        sa.Column("response_status", sa.String(length=30), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_class", sa.String(length=80), nullable=True),
        sa.Column("approved_by", sa.BigInteger(), sa.ForeignKey("admins.telegram_id"), nullable=True),
        sa.Column("message_id", sa.String(length=36), sa.ForeignKey("outbox.id"), nullable=True),
        sa.Column("draft_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "response_status IN ('ok', 'error', 'timeout', 'fallback', 'pending', 'approved', 'rejected', 'edited')"
        ),
        sa.CheckConstraint("mode IN ('draft_only', 'human_approval')"),
    )
    op.create_index("ix_llm_reply_audits_execution_id", "llm_reply_audits", ["execution_id"])
    op.create_index("ix_llm_reply_audits_run_id", "llm_reply_audits", ["run_id"])
    op.create_index("ix_llm_reply_audits_identity_id", "llm_reply_audits", ["identity_id"])
    op.create_index("ix_llm_reply_audits_contact_id", "llm_reply_audits", ["contact_id"])
    op.create_index("ix_llm_reply_audits_project_id", "llm_reply_audits", ["project_id"])
    op.create_index(
        "ix_llm_reply_audits_pending",
        "llm_reply_audits",
        ["project_id", "response_status", "created_at"],
    )


def downgrade():
    op.drop_index("ix_llm_reply_audits_pending", table_name="llm_reply_audits")
    op.drop_index("ix_llm_reply_audits_project_id", table_name="llm_reply_audits")
    op.drop_index("ix_llm_reply_audits_contact_id", table_name="llm_reply_audits")
    op.drop_index("ix_llm_reply_audits_identity_id", table_name="llm_reply_audits")
    op.drop_index("ix_llm_reply_audits_run_id", table_name="llm_reply_audits")
    op.drop_index("ix_llm_reply_audits_execution_id", table_name="llm_reply_audits")
    op.drop_table("llm_reply_audits")
