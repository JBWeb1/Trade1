"""foundation tables

Revision ID: 0001
Revises: 
Create Date: 2026-10-19 19:59:17.156216
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('app_settings',
    sa.Column('key', sa.String(length=100), nullable=False),
    sa.Column('value', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('audit_logs',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('actor', sa.String(length=64), nullable=False),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('details', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_logs_action'), 'audit_logs', ['action'], unique=False)
    op.create_index(op.f('ix_audit_logs_ts'), 'audit_logs', ['ts'], unique=False)
    op.create_table('health_events',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('component', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('details', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_health_events_ts'), 'health_events', ['ts'], unique=False)
    op.create_table('ingestion_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('symbol', sa.String(length=64), nullable=False),
    sa.Column('timeframe', sa.String(length=8), nullable=False),
    sa.Column('requested_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('requested_end', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('source', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('bars_received', sa.Integer(), nullable=False),
    sa.Column('bars_valid', sa.Integer(), nullable=False),
    sa.Column('bars_inserted', sa.Integer(), nullable=False),
    sa.Column('bars_already_present', sa.Integer(), nullable=False),
    sa.Column('first_bar', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_bar', sa.DateTime(timezone=True), nullable=True),
    sa.Column('quality', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_ingestion_symbol_tf', 'ingestion_runs', ['symbol', 'timeframe', 'started_at'], unique=False)
    op.create_table('instruments',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('broker_server', sa.String(length=100), nullable=False),
    sa.Column('symbol', sa.String(length=64), nullable=False),
    sa.Column('category', sa.String(length=32), nullable=False),
    sa.Column('subcategory', sa.String(length=32), nullable=True),
    sa.Column('description', sa.String(length=255), nullable=True),
    sa.Column('spec', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('issues', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('metadata_ok', sa.Boolean(), nullable=False),
    sa.Column('discovered_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('broker_server', 'symbol', name='uq_instrument_server_symbol')
    )
    op.create_index(op.f('ix_instruments_category'), 'instruments', ['category'], unique=False)
    op.create_table('bars',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('symbol', sa.String(length=64), nullable=False),
    sa.Column('timeframe', sa.String(length=8), nullable=False),
    sa.Column('time', sa.DateTime(timezone=True), nullable=False),
    sa.Column('open', sa.Float(), nullable=False),
    sa.Column('high', sa.Float(), nullable=False),
    sa.Column('low', sa.Float(), nullable=False),
    sa.Column('close', sa.Float(), nullable=False),
    sa.Column('tick_volume', sa.BigInteger(), nullable=False),
    sa.Column('real_volume', sa.BigInteger(), nullable=False),
    sa.Column('spread_points', sa.Integer(), nullable=False),
    sa.Column('ingestion_run_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['ingestion_run_id'], ['ingestion_runs.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('symbol', 'timeframe', 'time', name='uq_bar_symbol_tf_time')
    )


def downgrade() -> None:
    op.drop_table('bars')
    op.drop_index(op.f('ix_instruments_category'), table_name='instruments')
    op.drop_table('instruments')
    op.drop_index('ix_ingestion_symbol_tf', table_name='ingestion_runs')
    op.drop_table('ingestion_runs')
    op.drop_index(op.f('ix_health_events_ts'), table_name='health_events')
    op.drop_table('health_events')
    op.drop_index(op.f('ix_audit_logs_ts'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_action'), table_name='audit_logs')
    op.drop_table('audit_logs')
    op.drop_table('app_settings')
