"""validation reports and trade regime

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-20 16:14:22.306477
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('validation_reports',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('strategy_version_id', sa.Integer(), nullable=False),
    sa.Column('backtest_run_id', sa.Integer(), nullable=True),
    sa.Column('symbol', sa.String(length=64), nullable=False),
    sa.Column('timeframe', sa.String(length=8), nullable=False),
    sa.Column('htf', sa.String(length=8), nullable=True),
    sa.Column('start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('end', sa.DateTime(timezone=True), nullable=False),
    sa.Column('config', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('data_fingerprint', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('oos_key', sa.String(length=255), nullable=False),
    sa.Column('oos_prior_uses', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('verdict', sa.String(length=16), nullable=True),
    sa.Column('reasons', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('report', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['backtest_run_id'], ['backtest_runs.id'], ),
    sa.ForeignKeyConstraint(['strategy_version_id'], ['strategy_versions.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_validation_reports_oos_key'), 'validation_reports', ['oos_key'], unique=False)
    op.create_index(op.f('ix_validation_reports_strategy_version_id'), 'validation_reports', ['strategy_version_id'], unique=False)
    op.add_column('backtest_trades', sa.Column('regime', sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column('backtest_trades', 'regime')
    op.drop_index(op.f('ix_validation_reports_strategy_version_id'), table_name='validation_reports')
    op.drop_index(op.f('ix_validation_reports_oos_key'), table_name='validation_reports')
    op.drop_table('validation_reports')
