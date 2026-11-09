"""Email addresses share the durable outbox transport."""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"


def upgrade():
    with op.batch_alter_table("outbox") as batch:
        batch.alter_column("chat_id", existing_type=sa.String(80), type_=sa.String(250), existing_nullable=False)


def downgrade():
    # Existing email destinations must not be silently truncated.
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT COUNT(*) FROM outbox WHERE length(chat_id) > 80")).scalar():
        raise RuntimeError("Archive long email destinations before downgrading 0005")
    with op.batch_alter_table("outbox") as batch:
        batch.alter_column("chat_id", existing_type=sa.String(250), type_=sa.String(80), existing_nullable=False)
