"""Add business ownership, roles, session history and audit records."""

from alembic import op
import sqlalchemy as sa


revision = '0005_accounts_and_businesses'
down_revision = '0004_security_hardening'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'business',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.execute(sa.text(
        "INSERT INTO business (id, name) VALUES (1, 'DukaSmart Business')"
    ))

    op.add_column('user', sa.Column('business_id', sa.Integer(), nullable=True))
    op.add_column('user', sa.Column('role', sa.String(length=20), nullable=False, server_default='administrator'))
    op.add_column('user', sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column('user', sa.Column('email', sa.String(length=254), nullable=True))
    op.add_column('user', sa.Column('must_change_password', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('user', sa.Column('last_login_at', sa.DateTime(), nullable=True))

    for table in ('product', 'transaction', 'customer_debt', 'checkout_sale', 'stock_movement'):
        op.add_column(table, sa.Column('business_id', sa.Integer(), nullable=True))
    op.add_column('transaction', sa.Column('created_by_id', sa.Integer(), nullable=True))

    op.execute(sa.text('UPDATE "user" SET business_id = 1'))
    for table in ('product', 'transaction', 'customer_debt', 'checkout_sale', 'stock_movement'):
        op.execute(sa.text(f'UPDATE "{table}" SET business_id = 1'))
    op.execute(sa.text("""
        UPDATE "transaction"
        SET created_by_id = (
            SELECT checkout_sale.user_id
            FROM checkout_sale
            WHERE checkout_sale.id = "transaction".sale_id
        )
        WHERE sale_id IS NOT NULL
    """))

    for table in ('user', 'product', 'transaction', 'customer_debt', 'checkout_sale', 'stock_movement'):
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column(
                'business_id',
                existing_type=sa.Integer(),
                nullable=False,
            )
            batch_op.create_foreign_key(
                f'fk_{table}_business_id_business',
                'business',
                ['business_id'],
                ['id'],
            )
        op.create_index(f'ix_{table}_business_id', table, ['business_id'])
    with op.batch_alter_table('transaction') as batch_op:
        batch_op.create_foreign_key(
            'fk_transaction_created_by_id_user',
            'user',
            ['created_by_id'],
            ['id'],
        )

    op.create_table(
        'login_event',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=True),
        sa.Column('business_id', sa.Integer(), sa.ForeignKey('business.id'), nullable=True),
        sa.Column('username', sa.String(length=80), nullable=False),
        sa.Column('event_type', sa.String(length=30), nullable=False),
        sa.Column('ip_hash', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('ix_login_event_user_id', 'login_event', ['user_id'])
    op.create_index('ix_login_event_business_id', 'login_event', ['business_id'])
    op.create_index('ix_login_event_created_at', 'login_event', ['created_at'])

    op.create_table(
        'user_session',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('business_id', sa.Integer(), sa.ForeignKey('business.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('last_seen_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('ended_at', sa.DateTime(), nullable=True),
        sa.Column('ended_reason', sa.String(length=30), nullable=True),
        sa.Column('ip_hash', sa.String(length=64), nullable=True),
    )
    op.create_index('ix_user_session_user_id', 'user_session', ['user_id'])
    op.create_index('ix_user_session_business_id', 'user_session', ['business_id'])
    op.create_index('ix_user_session_expires_at', 'user_session', ['expires_at'])

    op.create_table(
        'password_reset',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False, unique=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('used_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_password_reset_user_id', 'password_reset', ['user_id'])

    op.create_table(
        'activity_log',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('business_id', sa.Integer(), sa.ForeignKey('business.id'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=True),
        sa.Column('action', sa.String(length=60), nullable=False),
        sa.Column('entity_type', sa.String(length=40), nullable=False),
        sa.Column('entity_id', sa.String(length=80), nullable=True),
        sa.Column('summary', sa.String(length=240), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('ix_activity_log_business_id', 'activity_log', ['business_id'])
    op.create_index('ix_activity_log_user_id', 'activity_log', ['user_id'])
    op.create_index('ix_activity_log_created_at', 'activity_log', ['created_at'])


def downgrade():
    raise RuntimeError('Account, audit and business history is retained; restore a verified backup to reverse this migration.')
