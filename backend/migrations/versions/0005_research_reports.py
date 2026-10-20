"""research reports

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-20 16:41:19.669464
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('research_reports',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('summary', sa.String(length=255), nullable=True),
    sa.Column('params', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('result', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_research_reports_created_at'), 'research_reports', ['created_at'], unique=False)
    op.create_index(op.f('ix_research_reports_kind'), 'research_reports', ['kind'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_research_reports_kind'), table_name='research_reports')
    op.drop_index(op.f('ix_research_reports_created_at'), table_name='research_reports')
    op.drop_table('research_reports')
