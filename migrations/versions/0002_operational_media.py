"""Media bank, persistent retry schedule and delivery ordering."""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"

def upgrade():
    op.add_column("inbox", sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.add_column("identities", sa.Column("name", sa.String(200), nullable=False, server_default=""))
    op.create_table("assets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("mime", sa.String(120), nullable=False, server_default=""),
        sa.Column("local_path", sa.String(200), nullable=True),
        sa.Column("connection_ids", sa.JSON(), nullable=False),
        sa.Column("telegram_refs", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.add_column("outbox", sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("outbox", sa.Column("due_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("outbox", sa.Column("position", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("outbox", sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
    op.execute("UPDATE outbox SET created_at=updated_at, due_at=updated_at")
    op.execute("""UPDATE outbox AS o SET position=t.n FROM (
        SELECT id, ROW_NUMBER() OVER (PARTITION BY run_id ORDER BY created_at,id) AS n
        FROM outbox WHERE run_id IS NOT NULL) t WHERE o.id=t.id""")
    op.alter_column("outbox", "created_at", nullable=False)
    op.alter_column("outbox", "due_at", nullable=False)
    op.create_index("ix_outbox_run_position", "outbox", ["run_id","position"])
    op.create_index("ix_outbox_status_due", "outbox", ["status","due_at"])

def downgrade():
    op.drop_index("ix_outbox_status_due", table_name="outbox")
    op.drop_index("ix_outbox_run_position", table_name="outbox")
    for name in ("attempts","position","due_at","created_at"):
        op.drop_column("outbox", name)
    op.drop_column("inbox", "available_at")
    op.drop_table("assets")
    op.drop_column("identities", "name")
