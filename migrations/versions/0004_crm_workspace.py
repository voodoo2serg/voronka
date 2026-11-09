"""Frozen, additive CRM schema; existing funnel records stay unchanged."""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"

def upgrade():
    op.create_table('crm_accounts',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('admin_id', sa.BigInteger(), sa.ForeignKey('admins.telegram_id'), primary_key=False, nullable=False),
        sa.Column('login', sa.String(length=120), primary_key=False, nullable=False),
        sa.Column('password_hash', sa.String(length=250), primary_key=False, nullable=False),
        sa.Column('email', sa.String(length=250), primary_key=False, nullable=False),
        sa.Column('email_verified', sa.Boolean(), primary_key=False, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
        sa.UniqueConstraint('admin_id'),
        sa.UniqueConstraint('login'),
    )
    op.create_table('crm_import_batches',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('project_id', sa.String(length=36), sa.ForeignKey('projects.id'), primary_key=False, nullable=False),
        sa.Column('fingerprint', sa.String(length=64), primary_key=False, nullable=False),
        sa.Column('mapping', sa.JSON(), primary_key=False, nullable=False),
        sa.Column('rows', sa.JSON(), primary_key=False, nullable=False),
        sa.Column('state', sa.String(length=30), primary_key=False, nullable=False),
        sa.Column('report', sa.JSON(), primary_key=False, nullable=False),
        sa.Column('actor_id', sa.BigInteger(), sa.ForeignKey('admins.telegram_id'), primary_key=False, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
        sa.UniqueConstraint('project_id', 'fingerprint'),
    )
    op.create_table('crm_memberships',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('admin_id', sa.BigInteger(), sa.ForeignKey('admins.telegram_id'), primary_key=False, nullable=False),
        sa.Column('project_id', sa.String(length=36), sa.ForeignKey('projects.id'), primary_key=False, nullable=False),
        sa.UniqueConstraint('admin_id', 'project_id'),
    )
    op.create_table('crm_products',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('project_id', sa.String(length=36), sa.ForeignKey('projects.id'), primary_key=False, nullable=False),
        sa.Column('name', sa.String(length=200), primary_key=False, nullable=False),
        sa.Column('kind', sa.String(length=40), primary_key=False, nullable=False),
        sa.Column('description', sa.Text(), primary_key=False, nullable=False),
        sa.Column('price_minor', sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column('currency', sa.String(length=3), primary_key=False, nullable=False),
        sa.Column('active', sa.Boolean(), primary_key=False, nullable=False),
        sa.Column('config', sa.JSON(), primary_key=False, nullable=False),
        sa.CheckConstraint('price_minor >= 0'),
    )
    op.create_index('ix_crm_products_project_id', 'crm_products', ['project_id'])
    op.create_table('crm_profiles',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('contact_id', sa.String(length=36), sa.ForeignKey('contacts.id'), primary_key=False, nullable=False),
        sa.Column('project_id', sa.String(length=36), sa.ForeignKey('projects.id'), primary_key=False, nullable=False),
        sa.Column('name', sa.String(length=200), primary_key=False, nullable=False),
        sa.Column('email', sa.String(length=250), primary_key=False, nullable=False),
        sa.Column('phone', sa.String(length=80), primary_key=False, nullable=False),
        sa.Column('company', sa.String(length=200), primary_key=False, nullable=False),
        sa.Column('source', sa.String(length=200), primary_key=False, nullable=False),
        sa.Column('tags', sa.JSON(), primary_key=False, nullable=False),
        sa.Column('extra', sa.JSON(), primary_key=False, nullable=False),
        sa.Column('owner_id', sa.BigInteger(), sa.ForeignKey('admins.telegram_id'), primary_key=False, nullable=True),
        sa.Column('stopped', sa.Boolean(), primary_key=False, nullable=False),
        sa.Column('revision', sa.Integer(), primary_key=False, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
        sa.UniqueConstraint('contact_id', 'project_id'),
    )
    op.create_index('ix_crm_profiles_project_id', 'crm_profiles', ['project_id'])
    op.create_table('crm_promos',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('project_id', sa.String(length=36), sa.ForeignKey('projects.id'), primary_key=False, nullable=False),
        sa.Column('code', sa.String(length=60), primary_key=False, nullable=False),
        sa.Column('percent', sa.Integer(), primary_key=False, nullable=False),
        sa.Column('max_uses', sa.Integer(), primary_key=False, nullable=False),
        sa.Column('reserved', sa.Integer(), primary_key=False, nullable=False),
        sa.Column('active', sa.Boolean(), primary_key=False, nullable=False),
        sa.CheckConstraint('percent > 0 AND percent <= 100'),
        sa.UniqueConstraint('project_id', 'code'),
        sa.CheckConstraint('max_uses > 0'),
    )
    op.create_table('crm_activities',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('profile_id', sa.String(length=36), sa.ForeignKey('crm_profiles.id'), primary_key=False, nullable=False),
        sa.Column('actor_id', sa.BigInteger(), sa.ForeignKey('admins.telegram_id'), primary_key=False, nullable=False),
        sa.Column('kind', sa.String(length=40), primary_key=False, nullable=False),
        sa.Column('channel', sa.String(length=30), primary_key=False, nullable=False),
        sa.Column('outcome', sa.String(length=200), primary_key=False, nullable=False),
        sa.Column('body', sa.Text(), primary_key=False, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
    )
    op.create_index('ix_crm_activities_profile_id', 'crm_activities', ['profile_id'])
    op.create_table('crm_campaigns',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('project_id', sa.String(length=36), sa.ForeignKey('projects.id'), primary_key=False, nullable=False),
        sa.Column('connection_id', sa.String(length=36), sa.ForeignKey('connections.id'), primary_key=False, nullable=False),
        sa.Column('title', sa.String(length=200), primary_key=False, nullable=False),
        sa.Column('text', sa.Text(), primary_key=False, nullable=False),
        sa.Column('profile_ids', sa.JSON(), primary_key=False, nullable=False),
        sa.Column('state', sa.String(length=30), primary_key=False, nullable=False),
        sa.Column('report', sa.JSON(), primary_key=False, nullable=False),
    )
    op.create_table('crm_deals',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('project_id', sa.String(length=36), sa.ForeignKey('projects.id'), primary_key=False, nullable=False),
        sa.Column('profile_id', sa.String(length=36), sa.ForeignKey('crm_profiles.id'), primary_key=False, nullable=False),
        sa.Column('product_id', sa.String(length=36), sa.ForeignKey('crm_products.id'), primary_key=False, nullable=True),
        sa.Column('title', sa.String(length=250), primary_key=False, nullable=False),
        sa.Column('stage', sa.String(length=40), primary_key=False, nullable=False),
        sa.Column('amount_minor', sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column('next_action', sa.String(length=500), primary_key=False, nullable=False),
        sa.Column('due_at', sa.DateTime(timezone=True), primary_key=False, nullable=True),
        sa.Column('owner_id', sa.BigInteger(), sa.ForeignKey('admins.telegram_id'), primary_key=False, nullable=True),
        sa.Column('revision', sa.Integer(), primary_key=False, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
        sa.CheckConstraint('amount_minor >= 0'),
    )
    op.create_index('ix_crm_deals_project_id', 'crm_deals', ['project_id'])
    op.create_index('ix_crm_deals_profile_id', 'crm_deals', ['profile_id'])
    op.create_table('crm_orders',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('project_id', sa.String(length=36), sa.ForeignKey('projects.id'), primary_key=False, nullable=False),
        sa.Column('profile_id', sa.String(length=36), sa.ForeignKey('crm_profiles.id'), primary_key=False, nullable=False),
        sa.Column('product_id', sa.String(length=36), sa.ForeignKey('crm_products.id'), primary_key=False, nullable=False),
        sa.Column('request_key', sa.String(length=100), primary_key=False, nullable=False),
        sa.Column('total_minor', sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column('snapshot', sa.JSON(), primary_key=False, nullable=False),
        sa.Column('installments', sa.JSON(), primary_key=False, nullable=False),
        sa.Column('state', sa.String(length=30), primary_key=False, nullable=False),
        sa.Column('academic_complete', sa.Boolean(), primary_key=False, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
        sa.UniqueConstraint('project_id', 'request_key'),
        sa.CheckConstraint('total_minor >= 0'),
    )
    op.create_index('ix_crm_orders_project_id', 'crm_orders', ['project_id'])
    op.create_table('crm_recovery',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('account_id', sa.String(length=36), sa.ForeignKey('crm_accounts.id'), primary_key=False, nullable=False),
        sa.Column('token_hash', sa.String(length=64), primary_key=False, nullable=False),
        sa.Column('purpose', sa.String(length=30), primary_key=False, nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
        sa.Column('consumed', sa.Boolean(), primary_key=False, nullable=False),
        sa.UniqueConstraint('token_hash'),
    )
    op.create_table('crm_access_grants',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('order_id', sa.String(length=36), sa.ForeignKey('crm_orders.id'), primary_key=False, nullable=False),
        sa.Column('resource', sa.String(length=200), primary_key=False, nullable=False),
        sa.Column('capability', sa.String(length=40), primary_key=False, nullable=False),
        sa.Column('min_paid_percent', sa.Integer(), primary_key=False, nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), primary_key=False, nullable=True),
        sa.UniqueConstraint('order_id', 'resource', 'capability'),
    )
    op.create_table('crm_invoices',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('order_id', sa.String(length=36), sa.ForeignKey('crm_orders.id'), primary_key=False, nullable=False),
        sa.Column('number', sa.String(length=16), primary_key=False, nullable=False),
        sa.Column('amount_minor', sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column('test', sa.Boolean(), primary_key=False, nullable=False),
        sa.Column('state', sa.String(length=30), primary_key=False, nullable=False),
        sa.UniqueConstraint('number'),
    )
    op.create_table('crm_payments',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('order_id', sa.String(length=36), sa.ForeignKey('crm_orders.id'), primary_key=False, nullable=False),
        sa.Column('provider', sa.String(length=30), primary_key=False, nullable=False),
        sa.Column('external_id', sa.String(length=160), primary_key=False, nullable=False),
        sa.Column('amount_minor', sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column('kind', sa.String(length=20), primary_key=False, nullable=False),
        sa.Column('evidence', sa.Text(), primary_key=False, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
        sa.CheckConstraint('amount_minor > 0'),
        sa.UniqueConstraint('provider', 'external_id'),
    )
    op.create_index('ix_crm_payments_order_id', 'crm_payments', ['order_id'])
    op.create_table('crm_work_items',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('profile_id', sa.String(length=36), sa.ForeignKey('crm_profiles.id'), primary_key=False, nullable=False),
        sa.Column('deal_id', sa.String(length=36), sa.ForeignKey('crm_deals.id'), primary_key=False, nullable=True),
        sa.Column('title', sa.String(length=300), primary_key=False, nullable=False),
        sa.Column('kind', sa.String(length=40), primary_key=False, nullable=False),
        sa.Column('state', sa.String(length=30), primary_key=False, nullable=False),
        sa.Column('due_at', sa.DateTime(timezone=True), primary_key=False, nullable=True),
        sa.Column('assignee_id', sa.BigInteger(), sa.ForeignKey('admins.telegram_id'), primary_key=False, nullable=True),
        sa.Column('result', sa.Text(), primary_key=False, nullable=False),
    )
    op.create_index('ix_crm_work_items_profile_id', 'crm_work_items', ['profile_id'])
    op.create_table('crm_campaign_recipients',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('campaign_id', sa.String(length=36), sa.ForeignKey('crm_campaigns.id'), primary_key=False, nullable=False),
        sa.Column('profile_id', sa.String(length=36), sa.ForeignKey('crm_profiles.id'), primary_key=False, nullable=False),
        sa.Column('outbox_id', sa.String(length=36), sa.ForeignKey('outbox.id'), primary_key=False, nullable=False),
        sa.UniqueConstraint('campaign_id', 'profile_id'),
    )

def downgrade():
    op.drop_table('crm_campaign_recipients')
    op.drop_table('crm_work_items')
    op.drop_table('crm_payments')
    op.drop_table('crm_invoices')
    op.drop_table('crm_access_grants')
    op.drop_table('crm_recovery')
    op.drop_table('crm_orders')
    op.drop_table('crm_deals')
    op.drop_table('crm_campaigns')
    op.drop_table('crm_activities')
    op.drop_table('crm_promos')
    op.drop_table('crm_profiles')
    op.drop_table('crm_products')
    op.drop_table('crm_memberships')
    op.drop_table('crm_import_batches')
    op.drop_table('crm_accounts')
