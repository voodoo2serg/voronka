"""CRM redesign: Project fields, Branch, Course M2M, ActionChain, staff alerts."""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"


def upgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name
    inspector = sa.inspect(bind)

    def has_table(name: str) -> bool:
        return name in inspector.get_table_names()

    def has_column(table: str, column: str) -> bool:
        if not has_table(table):
            return False
        return column in {c["name"] for c in inspector.get_columns(table)}

    # --- projects extensions ---
    if has_table("projects"):
        if not has_column("projects", "description"):
            op.add_column("projects", sa.Column("description", sa.String(800), server_default="", nullable=False))
        if not has_column("projects", "status"):
            op.add_column("projects", sa.Column("status", sa.String(30), server_default="active", nullable=False))
        if not has_column("projects", "is_system"):
            op.add_column("projects", sa.Column("is_system", sa.Boolean(), server_default=sa.false(), nullable=False))
        if not has_column("projects", "system_key"):
            op.add_column("projects", sa.Column("system_key", sa.String(40), nullable=True))
            op.create_unique_constraint("uq_projects_system_key", "projects", ["system_key"])
        if not has_column("projects", "created_at"):
            op.add_column("projects", sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))

    if not has_table("crm_branches"):
        op.create_table(
            "crm_branches",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False, index=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("description", sa.String(800), server_default="", nullable=False),
            sa.Column("status", sa.String(30), server_default="active", nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )

    # Refresh inspector after creates
    inspector = sa.inspect(bind)

    if has_table("crm_profiles"):
        if not has_column("crm_profiles", "branch_id"):
            op.add_column(
                "crm_profiles",
                sa.Column("branch_id", sa.String(36), sa.ForeignKey("crm_branches.id"), nullable=True),
            )
            op.create_index("ix_crm_profiles_branch_id", "crm_profiles", ["branch_id"])
        if not has_column("crm_profiles", "person_role"):
            op.add_column(
                "crm_profiles",
                sa.Column("person_role", sa.String(30), server_default="lead", nullable=False),
            )

    if not has_table("crm_project_courses"):
        op.create_table(
            "crm_project_courses",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False, index=True),
            sa.Column("course_id", sa.String(200), nullable=False),
            sa.Column("branch_id", sa.String(36), sa.ForeignKey("crm_branches.id"), nullable=True),
            sa.Column("label", sa.String(200), server_default="", nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("project_id", "course_id", name="uq_crm_project_courses_project_course"),
        )

    if not has_table("crm_action_chains"):
        op.create_table(
            "crm_action_chains",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False, index=True),
            sa.Column("branch_id", sa.String(36), sa.ForeignKey("crm_branches.id"), nullable=True, index=True),
            sa.Column("course_id", sa.String(200), nullable=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("description", sa.String(800), server_default="", nullable=False),
            sa.Column("state", sa.String(30), server_default="draft", nullable=False),
            sa.Column("nodes", sa.JSON(), nullable=False),
            sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
            sa.Column("funnel_id", sa.String(36), sa.ForeignKey("funnels.id"), nullable=True),
            sa.Column("actor_id", sa.BigInteger(), sa.ForeignKey("admins.telegram_id"), nullable=True),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )

    if not has_table("crm_action_chain_dry_runs"):
        op.create_table(
            "crm_action_chain_dry_runs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("chain_id", sa.String(36), sa.ForeignKey("crm_action_chains.id"), nullable=False, index=True),
            sa.Column("actor_id", sa.BigInteger(), sa.ForeignKey("admins.telegram_id"), nullable=False),
            sa.Column("email", sa.String(250), server_default="", nullable=False),
            sa.Column("telegram", sa.String(120), server_default="", nullable=False),
            sa.Column("report", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        )

    if not has_table("crm_staff_alert_rules"):
        op.create_table(
            "crm_staff_alert_rules",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=True, index=True),
            sa.Column("kind", sa.String(40), nullable=False),
            sa.Column("channel", sa.String(20), server_default="telegram", nullable=False),
            sa.Column("amount_minor", sa.BigInteger(), server_default="0", nullable=False),
            sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
            sa.Column("config", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )

    if not has_table("crm_staff_alert_events"):
        op.create_table(
            "crm_staff_alert_events",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("rule_id", sa.String(36), sa.ForeignKey("crm_staff_alert_rules.id"), nullable=False, index=True),
            sa.Column("dedupe_key", sa.String(200), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("state", sa.String(30), server_default="sent", nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("rule_id", "dedupe_key", name="uq_crm_staff_alert_events_rule_dedupe"),
        )

    # Seed «Старт» if missing (Postgres + SQLite).
    if dialect == "sqlite":
        rows = bind.execute(sa.text("SELECT id FROM projects WHERE system_key = 'start'")).fetchall()
    else:
        rows = bind.execute(sa.text("SELECT id FROM projects WHERE system_key = 'start'")).fetchall()
    if not rows:
        import uuid
        from datetime import datetime, timezone

        pid = str(uuid.uuid4())
        bind.execute(
            sa.text(
                "INSERT INTO projects (id, name, goal, description, status, is_system, system_key, created_at) "
                "VALUES (:id, :name, :goal, :description, 'active', :is_system, 'start', :created_at)"
            ),
            {
                "id": pid,
                "name": "Старт",
                "goal": "Непривязанный интерес: заявки до коммерческого проекта",
                "description": (
                    "Системный проект для людей, которые оставили заявку или email, "
                    "но ещё не привязаны к коммерческому Project / Branch."
                ),
                "is_system": True if dialect != "sqlite" else 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )


def downgrade():
    op.drop_table("crm_staff_alert_events")
    op.drop_table("crm_staff_alert_rules")
    op.drop_table("crm_action_chain_dry_runs")
    op.drop_table("crm_action_chains")
    op.drop_table("crm_project_courses")
    try:
        op.drop_index("ix_crm_profiles_branch_id", table_name="crm_profiles")
    except Exception:
        pass
    with op.batch_alter_table("crm_profiles") as batch:
        batch.drop_column("person_role")
        batch.drop_column("branch_id")
    op.drop_table("crm_branches")
    try:
        op.drop_constraint("uq_projects_system_key", "projects", type_="unique")
    except Exception:
        pass
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("created_at")
        batch.drop_column("system_key")
        batch.drop_column("is_system")
        batch.drop_column("status")
        batch.drop_column("description")
