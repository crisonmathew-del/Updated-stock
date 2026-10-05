"""Phase 1 data foundation: users, settings, tickers, daily bars (hypertable), corporate
actions, shares outstanding, job runs and data-quality issues.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-05 08:21:57.384093
"""

from collections.abc import Sequence

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = '0002'
down_revision: str | Sequence[str] | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('job_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('job_name', sa.String(length=64), nullable=False),
    sa.Column('trigger', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('stats', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_job_runs'))
    )
    op.create_index('ix_job_runs_job_started', 'job_runs', ['job_name', 'started_at'], unique=False)
    op.create_table('settings',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('value', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('key', name=op.f('pk_settings'))
    )
    op.create_table('tickers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('symbol', sa.String(length=16), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('exchange', sa.String(length=16), nullable=False),
    sa.Column('type', sa.String(length=16), nullable=False),
    sa.Column('is_benchmark', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('cik', sa.String(length=10), nullable=True),
    sa.Column('sic_code', sa.String(length=4), nullable=True),
    sa.Column('sic_description', sa.String(length=255), nullable=True),
    sa.Column('sector', sa.String(length=64), nullable=True),
    sa.Column('industry', sa.String(length=128), nullable=True),
    sa.Column('reference_refreshed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('market_cap', sa.Float(), nullable=True),
    sa.Column('float_shares', sa.Float(), nullable=True),
    sa.Column('first_seen', sa.Date(), nullable=False),
    sa.Column('last_seen', sa.Date(), nullable=False),
    sa.Column('listed_date', sa.Date(), nullable=True),
    sa.Column('delisted_date', sa.Date(), nullable=True),
    sa.Column('backfill_status', sa.String(length=16), server_default='pending', nullable=False),
    sa.Column('backfill_error', sa.Text(), nullable=True),
    sa.Column('backfilled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('bars_start', sa.Date(), nullable=True),
    sa.Column('bars_end', sa.Date(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_tickers'))
    )
    op.create_index('ix_tickers_cik', 'tickers', ['cik'], unique=False)
    op.create_index('uq_tickers_active_symbol', 'tickers', ['symbol'], unique=True, postgresql_where=sa.text('active'))
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('email', name=op.f('uq_users_email'))
    )
    op.create_table('corporate_actions',
    sa.Column('ticker_id', sa.Integer(), nullable=False),
    sa.Column('ex_date', sa.Date(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('value', sa.Float(), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['ticker_id'], ['tickers.id'], name=op.f('fk_corporate_actions_ticker_id_tickers'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('ticker_id', 'ex_date', 'kind', name=op.f('pk_corporate_actions'))
    )
    op.create_table('daily_bars',
    sa.Column('ticker_id', sa.Integer(), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('open', sa.Float(), nullable=False),
    sa.Column('high', sa.Float(), nullable=False),
    sa.Column('low', sa.Float(), nullable=False),
    sa.Column('close', sa.Float(), nullable=False),
    sa.Column('volume', sa.BigInteger(), nullable=False),
    sa.Column('vwap', sa.Float(), nullable=True),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.ForeignKeyConstraint(['ticker_id'], ['tickers.id'], name=op.f('fk_daily_bars_ticker_id_tickers'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('ticker_id', 'date', name=op.f('pk_daily_bars'))
    )
    # Partition daily bars by year; queries are per ticker over a date range or per date
    # across all tickers, both served by the (ticker_id, date) key and the default date index.
    op.execute("SELECT create_hypertable('daily_bars', by_range('date', INTERVAL '1 year'))")
    op.create_table('data_quality_issues',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('fingerprint', sa.String(length=160), nullable=False),
    sa.Column('check', sa.String(length=48), nullable=False),
    sa.Column('severity', sa.String(length=16), nullable=False),
    sa.Column('ticker_id', sa.Integer(), nullable=True),
    sa.Column('issue_date', sa.Date(), nullable=True),
    sa.Column('detail', sa.Text(), nullable=False),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('first_detected_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_detected_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['ticker_id'], ['tickers.id'], name=op.f('fk_data_quality_issues_ticker_id_tickers'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_data_quality_issues'))
    )
    op.create_index('ix_dq_open_severity', 'data_quality_issues', ['severity'], unique=False, postgresql_where=sa.text('resolved_at IS NULL'))
    op.create_index('uq_dq_open_fingerprint', 'data_quality_issues', ['fingerprint'], unique=True, postgresql_where=sa.text('resolved_at IS NULL'))
    op.create_table('shares_outstanding',
    sa.Column('ticker_id', sa.Integer(), nullable=False),
    sa.Column('as_of_date', sa.Date(), nullable=False),
    sa.Column('filed_date', sa.Date(), nullable=False),
    sa.Column('shares', sa.BigInteger(), nullable=False),
    sa.Column('form', sa.String(length=16), nullable=True),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.ForeignKeyConstraint(['ticker_id'], ['tickers.id'], name=op.f('fk_shares_outstanding_ticker_id_tickers'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('ticker_id', 'as_of_date', 'filed_date', name=op.f('pk_shares_outstanding'))
    )


def downgrade() -> None:
    op.drop_table('shares_outstanding')
    op.drop_index('uq_dq_open_fingerprint', table_name='data_quality_issues', postgresql_where=sa.text('resolved_at IS NULL'))
    op.drop_index('ix_dq_open_severity', table_name='data_quality_issues', postgresql_where=sa.text('resolved_at IS NULL'))
    op.drop_table('data_quality_issues')
    op.drop_table('daily_bars')
    op.drop_table('corporate_actions')
    op.drop_table('users')
    op.drop_index('uq_tickers_active_symbol', table_name='tickers', postgresql_where=sa.text('active'))
    op.drop_index('ix_tickers_cik', table_name='tickers')
    op.drop_table('tickers')
    op.drop_table('settings')
    op.drop_index('ix_job_runs_job_started', table_name='job_runs')
    op.drop_table('job_runs')
