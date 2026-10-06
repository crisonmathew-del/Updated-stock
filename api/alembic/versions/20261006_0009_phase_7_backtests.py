"""Phase 7: the backtest lab: cached candidate tapes (their Parquet files live under
BACKTEST_DIR) and backtest runs with their reports.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-06 19:11:16.228892
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0009'
down_revision: str | Sequence[str] | None = '0008'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('backtest_tapes',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('start', sa.Date(), nullable=False),
    sa.Column('end', sa.Date(), nullable=False),
    sa.Column('settings_hash', sa.String(length=64), nullable=False),
    sa.Column('settings', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('cells', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('path', sa.Text(), nullable=True),
    sa.Column('stats', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_backtest_tapes'))
    )
    op.create_index('ix_backtest_tapes_lookup', 'backtest_tapes', ['settings_hash', 'start', 'end'], unique=False)
    op.create_table('backtest_runs',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('params', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('tape_id', sa.BigInteger(), nullable=True),
    sa.Column('progress', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('summary', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('report', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('trades', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['tape_id'], ['backtest_tapes.id'], name=op.f('fk_backtest_runs_tape_id_backtest_tapes'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_backtest_runs_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_backtest_runs'))
    )
    op.create_index(op.f('ix_backtest_runs_user_id'), 'backtest_runs', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_backtest_runs_user_id'), table_name='backtest_runs')
    op.drop_table('backtest_runs')
    op.drop_index('ix_backtest_tapes_lookup', table_name='backtest_tapes')
    op.drop_table('backtest_tapes')
