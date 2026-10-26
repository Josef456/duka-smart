"""Add bar product units and an auditable stock movement ledger."""

from alembic import op
import sqlalchemy as sa


revision = '0002_bar_inventory'
down_revision = '0001_core_schema'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'product',
        sa.Column('category', sa.String(length=30), nullable=False, server_default='general'),
    )
    op.create_index('ix_product_category', 'product', ['category'])
    op.add_column(
        'product',
        sa.Column('base_unit', sa.String(length=30), nullable=False, server_default='unit'),
    )
    op.add_column(
        'product',
        sa.Column('purchase_unit', sa.String(length=30), nullable=False, server_default='unit'),
    )
    op.add_column(
        'product',
        sa.Column('units_per_purchase_unit', sa.Integer(), nullable=False, server_default='1'),
    )
    op.create_table(
        'stock_movement',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=False),
        sa.Column('transaction_id', sa.Integer(), nullable=True),
        sa.Column('movement_type', sa.String(length=20), nullable=False),
        sa.Column('quantity_delta', sa.Integer(), nullable=False),
        sa.Column('unit_cost', sa.Float(), nullable=True),
        sa.Column('reason', sa.String(length=240), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['created_by_id'], ['user.id']),
        sa.ForeignKeyConstraint(['product_id'], ['product.id']),
        sa.ForeignKeyConstraint(['transaction_id'], ['transaction.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_stock_movement_product_created_at',
        'stock_movement',
        ['product_id', 'created_at'],
    )
    op.create_index(
        'ix_stock_movement_created_at',
        'stock_movement',
        ['created_at'],
    )
    op.execute(sa.text("""
        INSERT INTO stock_movement
            (product_id, transaction_id, movement_type, quantity_delta, unit_cost, reason, created_at)
        SELECT
            t.product_id,
            t.id,
            CASE WHEN t.kind = 'sale' THEN 'sale' ELSE 'opening' END,
            CASE WHEN t.kind = 'sale' THEN -t.quantity ELSE t.quantity END,
            CASE
                WHEN t.kind = 'restock' AND t.quantity > 0
                    THEN t.amount / t.quantity
                WHEN t.kind = 'sale' THEN product.cost_price
                ELSE NULL
            END,
            CASE WHEN t.kind = 'sale' THEN 'Migrated historical sale'
                 ELSE 'Migrated historical opening stock' END,
            COALESCE(t.created_at, CURRENT_TIMESTAMP)
        FROM "transaction" AS t
        JOIN product ON product.id = t.product_id
        WHERE t.kind IN ('sale', 'restock')
          AND t.product_id IS NOT NULL
          AND t.quantity IS NOT NULL
          AND t.quantity > 0
    """))
    op.execute(sa.text("""
        INSERT INTO stock_movement
            (product_id, movement_type, quantity_delta, unit_cost, reason, created_at)
        SELECT
            product.id,
            'migration_balance',
            product.quantity - COALESCE(SUM(stock_movement.quantity_delta), 0),
            product.cost_price,
            'Migration balance snapshot; preserved existing on-hand quantity',
            CURRENT_TIMESTAMP
        FROM product
        LEFT JOIN stock_movement ON stock_movement.product_id = product.id
        GROUP BY product.id, product.quantity, product.cost_price
        HAVING product.quantity != COALESCE(SUM(stock_movement.quantity_delta), 0)
    """))


def downgrade():
    raise RuntimeError('Bar stock history and unit settings are retained; restore a backup to reverse this migration.')
