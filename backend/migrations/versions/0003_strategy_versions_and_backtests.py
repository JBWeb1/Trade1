"""strategy versions and backtests

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-20 16:02:22.562401
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('strategy_versions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('version', sa.String(length=16), nullable=False),
    sa.Column('params', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('params_hash', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name', 'version', 'params_hash', name='uq_strategy_version')
    )
    op.create_table('backtest_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('strategy_version_id', sa.Integer(), nullable=False),
    sa.Column('symbol', sa.String(length=64), nullable=False),
    sa.Column('timeframe', sa.String(length=8), nullable=False),
    sa.Column('htf', sa.String(length=8), nullable=True),
    sa.Column('start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('end', sa.DateTime(timezone=True), nullable=False),
    sa.Column('config', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('data_fingerprint', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('result_kind', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('metrics', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('equity_curve', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['strategy_version_id'], ['strategy_versions.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_backtest_runs_strategy_version_id'), 'backtest_runs', ['strategy_version_id'], unique=False)
    op.create_index(op.f('ix_backtest_runs_symbol'), 'backtest_runs', ['symbol'], unique=False)
    op.create_table('backtest_trades',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('side', sa.String(length=4), nullable=False),
    sa.Column('signal_time', sa.DateTime(timezone=True), nullable=False),
    sa.Column('entry_time', sa.DateTime(timezone=True), nullable=False),
    sa.Column('exit_time', sa.DateTime(timezone=True), nullable=True),
    sa.Column('entry_price', sa.Float(), nullable=False),
    sa.Column('exit_price', sa.Float(), nullable=True),
    sa.Column('volume', sa.Float(), nullable=False),
    sa.Column('stop_loss', sa.Float(), nullable=False),
    sa.Column('take_profit', sa.Float(), nullable=False),
    sa.Column('exit_reason', sa.String(length=64), nullable=False),
    sa.Column('gross_pnl', sa.Float(), nullable=False),
    sa.Column('commission', sa.Float(), nullable=False),
    sa.Column('swap', sa.Float(), nullable=False),
    sa.Column('spread_cost', sa.Float(), nullable=False),
    sa.Column('slippage_cost', sa.Float(), nullable=False),
    sa.Column('net_pnl', sa.Float(), nullable=False),
    sa.Column('planned_risk_money', sa.Float(), nullable=False),
    sa.Column('bars_held', sa.Integer(), nullable=False),
    sa.Column('ambiguous_bar', sa.Boolean(), nullable=False),
    sa.Column('rationale', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['backtest_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_backtest_trades_run_id'), 'backtest_trades', ['run_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_backtest_trades_run_id'), table_name='backtest_trades')
    op.drop_table('backtest_trades')
    op.drop_index(op.f('ix_backtest_runs_symbol'), table_name='backtest_runs')
    op.drop_index(op.f('ix_backtest_runs_strategy_version_id'), table_name='backtest_runs')
    op.drop_table('backtest_runs')
    op.drop_table('strategy_versions')
