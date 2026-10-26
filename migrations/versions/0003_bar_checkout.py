"""Add itemized bar checkout and payment records."""

from alembic import op
import sqlalchemy as sa


revision = '0003_bar_checkout'
down_revision = '0002_bar_inventory'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'checkout_sale',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('idempotency_key', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('subtotal', sa.Float(), nullable=False),
        sa.Column('discount_total', sa.Float(), nullable=False),
        sa.Column('total', sa.Float(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_checkout_sale_idempotency_key',
        'checkout_sale',
        ['idempotency_key'],
        unique=True,
    )
    op.create_table(
        'checkout_line',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('sale_id', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=False),
        sa.Column('product_name', sa.String(length=160), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('unit_price', sa.Float(), nullable=False),
        sa.Column('unit_cost', sa.Float(), nullable=False),
        sa.Column('discount_amount', sa.Float(), nullable=False),
        sa.Column('line_total', sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(['product_id'], ['product.id']),
        sa.ForeignKeyConstraint(['sale_id'], ['checkout_sale.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_checkout_line_sale_id', 'checkout_line', ['sale_id'])
    op.create_table(
        'sale_payment',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('sale_id', sa.Integer(), nullable=False),
        sa.Column('method', sa.String(length=30), nullable=False),
        sa.Column('amount', sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(['sale_id'], ['checkout_sale.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_sale_payment_sale_id', 'sale_payment', ['sale_id'])
    sale_id = sa.Column(
        'sale_id',
        sa.Integer(),
        sa.ForeignKey('checkout_sale.id', name='fk_transaction_sale_id_checkout_sale'),
        nullable=True,
    )
    if op.get_context().dialect.name == 'sqlite':
        naming_convention = {
            'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s',
            'pk': 'pk_%(table_name)s',
        }
        with op.batch_alter_table(
            'transaction',
            recreate='always',
            naming_convention=naming_convention,
        ) as batch_op:
            batch_op.add_column(sale_id)
    else:
        op.add_column('transaction', sale_id)
    op.create_index('ix_transaction_sale_id', 'transaction', ['sale_id'])


def downgrade():
    raise RuntimeError('Bar checkout and payment records are retained; restore a backup to reverse this migration.')
