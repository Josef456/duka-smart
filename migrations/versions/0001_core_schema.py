"""Baseline existing DukaSmart tables and preserve offline sale IDs."""

from alembic import op
import sqlalchemy as sa


revision = '0001_core_schema'
down_revision = None
branch_labels = None
depends_on = None


def _tables():
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade():
    tables = _tables()
    if 'user' not in tables:
        op.create_table(
            'user',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('username', sa.String(length=80), nullable=False),
            sa.Column('password_hash', sa.String(length=255), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('username'),
        )
    if 'product' not in tables:
        op.create_table(
            'product',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(length=160), nullable=False),
            sa.Column('sku', sa.String(length=80), nullable=False),
            sa.Column('cost_price', sa.Float(), nullable=False),
            sa.Column('selling_price', sa.Float(), nullable=False),
            sa.Column('quantity', sa.Integer(), nullable=False),
            sa.Column('low_stock_at', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('sku'),
        )
    if 'transaction' not in tables:
        op.create_table(
            'transaction',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('kind', sa.String(length=20), nullable=False),
            sa.Column('description', sa.String(length=240), nullable=False),
            sa.Column('amount', sa.Float(), nullable=False),
            sa.Column('quantity', sa.Integer(), nullable=True),
            sa.Column('product_id', sa.Integer(), nullable=True),
            sa.Column('payment_method', sa.String(length=30), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['product_id'], ['product.id']),
            sa.PrimaryKeyConstraint('id'),
        )
    if 'customer_debt' not in tables:
        op.create_table(
            'customer_debt',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('customer_name', sa.String(length=160), nullable=False),
            sa.Column('phone', sa.String(length=40), nullable=True),
            sa.Column('description', sa.String(length=240), nullable=False),
            sa.Column('amount', sa.Float(), nullable=False),
            sa.Column('amount_paid', sa.Float(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )

    transaction_columns = {
        column['name'] for column in sa.inspect(op.get_bind()).get_columns('transaction')
    }
    if 'sync_id' not in transaction_columns:
        op.add_column('transaction', sa.Column('sync_id', sa.String(length=36), nullable=True))

    inspector = sa.inspect(op.get_bind())
    unique_sync_constraint = any(
        constraint.get('column_names') == ['sync_id']
        for constraint in inspector.get_unique_constraints('transaction')
    )
    unique_sync_index = any(
        index.get('column_names') == ['sync_id'] and index.get('unique')
        for index in inspector.get_indexes('transaction')
    )
    if not unique_sync_constraint and not unique_sync_index:
        op.create_index(
            'ix_transaction_sync_id',
            'transaction',
            ['sync_id'],
            unique=True,
        )


def downgrade():
    raise RuntimeError('The core baseline migration is intentionally irreversible.')
