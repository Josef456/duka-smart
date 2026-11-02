"""Add business-scoped external API credentials."""

from alembic import op
import sqlalchemy as sa


revision = '0006_external_api_keys'
down_revision = '0005_accounts_and_businesses'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'api_key',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('key_hash', sa.String(length=64), nullable=False, unique=True),
        sa.Column('key_prefix', sa.String(length=24), nullable=False),
        sa.Column('scopes', sa.String(length=160), nullable=False),
        sa.Column('business_id', sa.Integer(), sa.ForeignKey('business.id'), nullable=False),
        sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.Column('rate_window_started_at', sa.DateTime(), nullable=True),
        sa.Column('rate_request_count', sa.Integer(), nullable=False, server_default='0'),
    )
    op.create_index('ix_api_key_business_id', 'api_key', ['business_id'])


def downgrade():
    op.drop_index('ix_api_key_business_id', table_name='api_key')
    op.drop_table('api_key')
