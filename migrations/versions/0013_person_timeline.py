"""VR-452: PersonTimelineEvent for CRM История tab."""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "crm_person_timeline_events" in inspector.get_table_names():
        return
    op.create_table(
        "crm_person_timeline_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("person_id", sa.String(36), sa.ForeignKey("crm_persons.id"), nullable=False, index=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=True, index=True),
        sa.Column("branch_id", sa.String(36), sa.ForeignKey("crm_branches.id"), nullable=True, index=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("source", sa.String(40), server_default="voronka", nullable=False),
        sa.Column("channel", sa.String(30), server_default="system", nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("kind", sa.String(80), nullable=False),
        sa.Column("title", sa.String(300), server_default="", nullable=False),
        sa.Column("summary", sa.String(800), server_default="", nullable=False),
        sa.Column("payload_ref", sa.JSON(), nullable=False),
        sa.Column("external_key", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source", "external_key", name="uq_crm_person_timeline_source_key"),
        sa.CheckConstraint("category IN ('correspondence', 'technical')", name="ck_crm_person_timeline_category"),
    )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "crm_person_timeline_events" in inspector.get_table_names():
        op.drop_table("crm_person_timeline_events")
