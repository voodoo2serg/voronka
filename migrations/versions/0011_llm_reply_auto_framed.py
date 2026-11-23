"""VR-406: allow llm_reply mode auto_framed on audit rows."""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"


def upgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "sqlite":
        # SQLite recreate is heavy; tests use create_all from models. Prod is Postgres.
        return
    rows = bind.execute(
        sa.text(
            """
            SELECT con.conname
            FROM pg_constraint con
            JOIN pg_class rel ON rel.oid = con.conrelid
            WHERE rel.relname = 'llm_reply_audits'
              AND con.contype = 'c'
              AND pg_get_constraintdef(con.oid) LIKE '%draft_only%'
              AND pg_get_constraintdef(con.oid) LIKE '%human_approval%'
              AND pg_get_constraintdef(con.oid) NOT LIKE '%auto_framed%'
            """
        )
    ).fetchall()
    for (name,) in rows:
        op.drop_constraint(name, "llm_reply_audits", type_="check")
    # Idempotent if already applied under this name
    existing = {
        c["name"] for c in sa.inspect(bind).get_check_constraints("llm_reply_audits")
    }
    if "llm_reply_audits_mode_check" not in existing:
        op.create_check_constraint(
            "llm_reply_audits_mode_check",
            "llm_reply_audits",
            "mode IN ('draft_only', 'human_approval', 'auto_framed')",
        )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    existing = {
        c["name"] for c in sa.inspect(bind).get_check_constraints("llm_reply_audits")
    }
    if "llm_reply_audits_mode_check" in existing:
        op.drop_constraint("llm_reply_audits_mode_check", "llm_reply_audits", type_="check")
    op.create_check_constraint(
        "llm_reply_audits_mode_check",
        "llm_reply_audits",
        "mode IN ('draft_only', 'human_approval')",
    )
