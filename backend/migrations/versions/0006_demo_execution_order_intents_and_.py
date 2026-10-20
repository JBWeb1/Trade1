"""demo execution: order intents and reconciliation

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-20 16:57:53.799068
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('order_intents',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('idempotency_key', sa.String(length=200), nullable=False),
    sa.Column('kind', sa.String(length=8), nullable=False),
    sa.Column('symbol', sa.String(length=64), nullable=False),
    sa.Column('side', sa.String(length=4), nullable=False),
    sa.Column('volume', sa.Float(), nullable=False),
    sa.Column('stop_loss', sa.Float(), nullable=True),
    sa.Column('take_profit', sa.Float(), nullable=True),
    sa.Column('requested_price', sa.Float(), nullable=True),
    sa.Column('position_ticket', sa.BigInteger(), nullable=True),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('strategy_version_id', sa.Integer(), nullable=True),
    sa.Column('signal', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('checks', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('account', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('request', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('response', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('fill_price', sa.Float(), nullable=True),
    sa.Column('slippage_points', sa.Float(), nullable=True),
    sa.ForeignKeyConstraint(['strategy_version_id'], ['strategy_versions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key')
    )
    op.create_index(op.f('ix_order_intents_created_at'), 'order_intents', ['created_at'], unique=False)
    op.create_index(op.f('ix_order_intents_status'), 'order_intents', ['status'], unique=False)
    op.create_index(op.f('ix_order_intents_symbol'), 'order_intents', ['symbol'], unique=False)
    op.create_table('reconciliation_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('kind', sa.String(length=48), nullable=False),
    sa.Column('order_intent_id', sa.Integer(), nullable=True),
    sa.Column('details', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('resolved', sa.Boolean(), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolution', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['order_intent_id'], ['order_intents.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_reconciliation_events_created_at'), 'reconciliation_events', ['created_at'], unique=False)
    op.create_index(op.f('ix_reconciliation_events_resolved'), 'reconciliation_events', ['resolved'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_reconciliation_events_resolved'), table_name='reconciliation_events')
    op.drop_index(op.f('ix_reconciliation_events_created_at'), table_name='reconciliation_events')
    op.drop_table('reconciliation_events')
    op.drop_index(op.f('ix_order_intents_symbol'), table_name='order_intents')
    op.drop_index(op.f('ix_order_intents_status'), table_name='order_intents')
    op.drop_index(op.f('ix_order_intents_created_at'), table_name='order_intents')
    op.drop_table('order_intents')
