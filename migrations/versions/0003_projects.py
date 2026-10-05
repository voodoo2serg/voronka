"""Projects group accounts. Funnels and connections can belong to one."""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"

def upgrade():
    op.create_table("projects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("goal", sa.String(400), nullable=False, server_default=""))
    op.add_column("connections", sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=True))
    op.add_column("connections", sa.Column("role", sa.String(20), nullable=False, server_default="bot"))
    op.add_column("funnels", sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=True))

def downgrade():
    op.drop_column("funnels", "project_id")
    op.drop_column("connections", "role")
    op.drop_column("connections", "project_id")
    op.drop_table("projects")
