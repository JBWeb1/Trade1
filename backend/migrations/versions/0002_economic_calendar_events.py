"""economic calendar events

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-20 04:25:40.068931
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('economic_events',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('event_time', sa.DateTime(timezone=True), nullable=False),
    sa.Column('currency', sa.String(length=8), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('impact', sa.String(length=16), nullable=False),
    sa.Column('forecast', sa.String(length=32), nullable=True),
    sa.Column('previous', sa.String(length=32), nullable=True),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider', 'event_time', 'currency', 'title', name='uq_event_provider_time_ccy_title')
    )
    op.create_index(op.f('ix_economic_events_currency'), 'economic_events', ['currency'], unique=False)
    op.create_index(op.f('ix_economic_events_event_time'), 'economic_events', ['event_time'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_economic_events_event_time'), table_name='economic_events')
    op.drop_index(op.f('ix_economic_events_currency'), table_name='economic_events')
    op.drop_table('economic_events')
