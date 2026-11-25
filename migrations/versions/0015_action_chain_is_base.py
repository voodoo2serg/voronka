"""CRM-R5: ActionChain.is_base — Save sets Project/Branch base chain.

One base per scope enforced by partial unique indexes:
- project-level: (project_id) WHERE is_base AND branch_id IS NULL
- branch-level: (project_id, branch_id) WHERE is_base AND branch_id IS NOT NULL
"""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"

PROJECT_BASE_IX = "uq_crm_action_chains_project_base"
BRANCH_BASE_IX = "uq_crm_action_chains_branch_base"
LEGACY_IS_BASE_IX = "ix_crm_action_chains_is_base"


def _index_names(inspector, table: str) -> set[str]:
    if table not in inspector.get_table_names():
        return set()
    return {ix["name"] for ix in inspector.get_indexes(table) if ix.get("name")}


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

    inspector = sa.inspect(bind)
    names = _index_names(inspector, "crm_action_chains")
    # Drop non-unique leftover from earlier 0015 drafts.
    if LEGACY_IS_BASE_IX in names:
        op.drop_index(LEGACY_IS_BASE_IX, table_name="crm_action_chains")
        names.discard(LEGACY_IS_BASE_IX)

    dialect = bind.dialect.name
    if dialect == "sqlite":
        project_where = sa.text("is_base = 1 AND branch_id IS NULL")
        branch_where = sa.text("is_base = 1 AND branch_id IS NOT NULL")
    else:
        project_where = sa.text("is_base IS true AND branch_id IS NULL")
        branch_where = sa.text("is_base IS true AND branch_id IS NOT NULL")

    # Deduplicate before unique indexes (keep newest per scope).
    conn = op.get_bind()
    bases = conn.execute(
        sa.text(
            "SELECT id, project_id, branch_id FROM crm_action_chains "
            "WHERE is_base ORDER BY updated_at DESC, id DESC"
        )
    ).fetchall()
    seen: set[tuple[str, str | None]] = set()
    for row in bases:
        key = (row.project_id, row.branch_id)
        if key in seen:
            conn.execute(
                sa.text("UPDATE crm_action_chains SET is_base = false WHERE id = :id"),
                {"id": row.id},
            )
        else:
            seen.add(key)

    def _unique_partial(name: str, columns: list[str], where):
        if name in names:
            return
        kw = {"sqlite_where": where} if dialect == "sqlite" else {"postgresql_where": where}
        op.create_index(name, "crm_action_chains", columns, unique=True, **kw)

    _unique_partial(PROJECT_BASE_IX, ["project_id"], project_where)
    _unique_partial(BRANCH_BASE_IX, ["project_id", "branch_id"], branch_where)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "crm_action_chains" not in inspector.get_table_names():
        return
    names = _index_names(inspector, "crm_action_chains")
    if BRANCH_BASE_IX in names:
        op.drop_index(BRANCH_BASE_IX, table_name="crm_action_chains")
    if PROJECT_BASE_IX in names:
        op.drop_index(PROJECT_BASE_IX, table_name="crm_action_chains")
    cols = {c["name"] for c in inspector.get_columns("crm_action_chains")}
    if "is_base" in cols:
        op.drop_column("crm_action_chains", "is_base")
