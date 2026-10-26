"""Persist sign-in throttling state without storing client IP addresses."""

from alembic import op
import sqlalchemy as sa


revision = '0004_security_hardening'
down_revision = '0003_bar_checkout'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'login_attempt',
        sa.Column('ip_hash', sa.String(length=64), nullable=False),
        sa.Column('failed_attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('window_started_at', sa.DateTime(), nullable=False),
        sa.Column('locked_until', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('ip_hash'),
    )


def downgrade():
    op.drop_table('login_attempt')
