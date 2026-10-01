"""Initial schema using a frozen v1 snapshot, independent of evolving app models."""
import os
from alembic import op
from migrations.schema_v1 import Base, Admin

revision = "0001"
down_revision = None

def upgrade():
    bind = op.get_bind()
    Base.metadata.create_all(bind)
    op.create_index("ix_inbox_status_created", "inbox", ["status", "created_at"])
    op.create_index("ix_outbox_status_updated", "outbox", ["status", "updated_at"])
    op.create_index("ix_runs_status_due", "runs", ["status", "due_at"])
    op.create_index("ix_events_connection_created", "events", ["connection_id", "created_at"])
    owner = int(os.environ["OWNER_TELEGRAM_ID"])
    if owner <= 0:
        raise ValueError("OWNER_TELEGRAM_ID must be positive")
    bind.execute(Admin.__table__.insert().values(
        telegram_id=owner, role="owner", active=True, connection_ids=[]))

def downgrade():
    Base.metadata.drop_all(op.get_bind())
