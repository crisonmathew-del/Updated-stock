"""Phase 2 analytics: indicators_daily (hypertable, compressed after 180 days), industry
groups and daily ranks, market breadth and regime; compression on daily_bars too.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-05 09:20:22.240433
"""

from collections.abc import Sequence

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = '0003'
down_revision: str | Sequence[str] | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('industry_groups',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('code', sa.String(length=16), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('sector', sa.String(length=48), nullable=False),
    sa.Column('sic_level', sa.SmallInteger(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_industry_groups')),
    sa.UniqueConstraint('code', name=op.f('uq_industry_groups_code'))
    )
    op.create_table('market_breadth_daily',
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('members', sa.Integer(), nullable=False),
    sa.Column('with_50', sa.Integer(), nullable=False),
    sa.Column('above_50', sa.Integer(), nullable=False),
    sa.Column('with_200', sa.Integer(), nullable=False),
    sa.Column('above_200', sa.Integer(), nullable=False),
    sa.Column('new_highs', sa.Integer(), nullable=False),
    sa.Column('new_lows', sa.Integer(), nullable=False),
    sa.Column('advancers', sa.Integer(), nullable=False),
    sa.Column('decliners', sa.Integer(), nullable=False),
    sa.Column('pct_above_50', sa.Float(), nullable=True),
    sa.Column('pct_above_200', sa.Float(), nullable=True),
    sa.Column('net_new_highs', sa.Integer(), nullable=False),
    sa.Column('ad_line', sa.Float(), nullable=False),
    sa.PrimaryKeyConstraint('date', name=op.f('pk_market_breadth_daily'))
    )
    op.create_table('market_regime_daily',
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('index_symbol', sa.String(length=8), nullable=False),
    sa.Column('state', sa.String(length=32), nullable=False),
    sa.Column('close', sa.Float(), nullable=True),
    sa.Column('ema21', sa.Float(), nullable=True),
    sa.Column('sma50', sa.Float(), nullable=True),
    sa.Column('sma200', sa.Float(), nullable=True),
    sa.Column('change_pct', sa.Float(), nullable=True),
    sa.Column('is_distribution_day', sa.Boolean(), nullable=False),
    sa.Column('distribution_days', sa.Integer(), nullable=False),
    sa.Column('distribution_dates', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('rally_day', sa.Integer(), nullable=True),
    sa.Column('rally_low', sa.Float(), nullable=True),
    sa.Column('is_ftd', sa.Boolean(), nullable=False),
    sa.Column('last_ftd_date', sa.Date(), nullable=True),
    sa.Column('pct_above_50', sa.Float(), nullable=True),
    sa.Column('changed_from', sa.String(length=32), nullable=True),
    sa.Column('reasons', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.PrimaryKeyConstraint('date', 'index_symbol', name=op.f('pk_market_regime_daily'))
    )
    op.create_table('group_rank_history',
    sa.Column('group_id', sa.Integer(), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('rank', sa.SmallInteger(), nullable=False),
    sa.Column('score', sa.Float(), nullable=False),
    sa.Column('members', sa.Integer(), nullable=False),
    sa.Column('median_rs', sa.Float(), nullable=True),
    sa.Column('return_3m', sa.Float(), nullable=True),
    sa.Column('return_6m', sa.Float(), nullable=True),
    sa.Column('tt_passing', sa.Integer(), nullable=False),
    sa.Column('new_highs', sa.Integer(), nullable=False),
    sa.Column('rank_change_4w', sa.SmallInteger(), nullable=True),
    sa.ForeignKeyConstraint(['group_id'], ['industry_groups.id'], name=op.f('fk_group_rank_history_group_id_industry_groups'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('group_id', 'date', name=op.f('pk_group_rank_history'))
    )
    op.create_table('indicators_daily',
    sa.Column('ticker_id', sa.Integer(), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('ema10', sa.Float(), nullable=True),
    sa.Column('ema21', sa.Float(), nullable=True),
    sa.Column('sma50', sa.Float(), nullable=True),
    sa.Column('sma150', sa.Float(), nullable=True),
    sa.Column('sma200', sa.Float(), nullable=True),
    sa.Column('sma50_slope', sa.Float(), nullable=True),
    sa.Column('sma150_slope', sa.Float(), nullable=True),
    sa.Column('sma200_slope', sa.Float(), nullable=True),
    sa.Column('atr14', sa.Float(), nullable=True),
    sa.Column('atr_ratio_10_50', sa.Float(), nullable=True),
    sa.Column('bb_width', sa.Float(), nullable=True),
    sa.Column('high_52w', sa.Float(), nullable=True),
    sa.Column('low_52w', sa.Float(), nullable=True),
    sa.Column('close_high_52w', sa.Float(), nullable=True),
    sa.Column('history_sessions', sa.Integer(), nullable=True),
    sa.Column('avg_volume_50', sa.Float(), nullable=True),
    sa.Column('avg_dollar_volume_50', sa.Float(), nullable=True),
    sa.Column('volume_ratio', sa.Float(), nullable=True),
    sa.Column('up_down_volume_50', sa.Float(), nullable=True),
    sa.Column('roc_63', sa.Float(), nullable=True),
    sa.Column('roc_126', sa.Float(), nullable=True),
    sa.Column('roc_189', sa.Float(), nullable=True),
    sa.Column('roc_252', sa.Float(), nullable=True),
    sa.Column('rs_raw', sa.Float(), nullable=True),
    sa.Column('rs_rating', sa.SmallInteger(), nullable=True),
    sa.Column('rs_line', sa.Float(), nullable=True),
    sa.Column('rs_line_high_52w', sa.Boolean(), nullable=True),
    sa.Column('rs_new_high_ahead', sa.Boolean(), nullable=True),
    sa.Column('rs_slope_21', sa.Float(), nullable=True),
    sa.Column('rs_slope_63', sa.Float(), nullable=True),
    sa.Column('stage', sa.SmallInteger(), nullable=True),
    sa.ForeignKeyConstraint(['ticker_id'], ['tickers.id'], name=op.f('fk_indicators_daily_ticker_id_tickers'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('ticker_id', 'date', name=op.f('pk_indicators_daily'))
    )
    op.execute("SELECT create_hypertable('indicators_daily', by_range('date', INTERVAL '1 year'))")
    # Columnar compression for chunks older than ~6 months: segment by ticker, order by date.
    # Updates and deletes still work on compressed chunks (e.g. after a split re-fetch).
    for table in ("indicators_daily", "daily_bars"):
        op.execute(
            f"ALTER TABLE {table} SET (timescaledb.enable_columnstore = true, "
            "timescaledb.segmentby = 'ticker_id', timescaledb.orderby = 'date')"
        )
        op.execute(f"CALL add_columnstore_policy('{table}', after => INTERVAL '180 days')")
    # Bulk loads arrive here (via ADBC binary COPY) before being merged into indicators_daily.
    op.execute(
        "CREATE UNLOGGED TABLE staging_indicators_daily "
        "(LIKE indicators_daily INCLUDING DEFAULTS)"
    )
    op.add_column('tickers', sa.Column('industry_group_id', sa.Integer(), nullable=True))
    op.add_column('tickers', sa.Column('indicators_stale', sa.Boolean(), server_default='true', nullable=False))
    op.alter_column('tickers', 'industry',
               existing_type=sa.VARCHAR(length=128),
               type_=sa.String(length=160),
               existing_nullable=True)
    op.create_foreign_key(op.f('fk_tickers_industry_group_id_industry_groups'), 'tickers', 'industry_groups', ['industry_group_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS staging_indicators_daily")
    op.execute("CALL remove_columnstore_policy('daily_bars', if_exists => true)")
    op.drop_constraint(op.f('fk_tickers_industry_group_id_industry_groups'), 'tickers', type_='foreignkey')
    op.alter_column('tickers', 'industry',
               existing_type=sa.String(length=160),
               type_=sa.VARCHAR(length=128),
               existing_nullable=True)
    op.drop_column('tickers', 'indicators_stale')
    op.drop_column('tickers', 'industry_group_id')
    op.drop_table('indicators_daily')
    op.drop_table('group_rank_history')
    op.drop_table('market_regime_daily')
    op.drop_table('market_breadth_daily')
    op.drop_table('industry_groups')
