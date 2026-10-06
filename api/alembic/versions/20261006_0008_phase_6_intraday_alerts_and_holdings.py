"""Phase 6: one-minute bars for watched names (hypertable, kept 30 days), the learned
time-of-day volume curve, alert rules, the alert log and the holdings tracker.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-06 10:11:18.521459
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0008'
down_revision: str | Sequence[str] | None = '0007'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('volume_profiles',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('sessions', sa.Integer(), nullable=False),
    sa.Column('symbols', sa.Integer(), nullable=False),
    sa.Column('cumulative', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_volume_profiles'))
    )
    op.create_table('alert_rules',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('enabled', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('scope', sa.String(length=16), nullable=False),
    sa.Column('ticker_id', sa.Integer(), nullable=True),
    sa.Column('watchlist_id', sa.BigInteger(), nullable=True),
    sa.Column('screen_id', sa.BigInteger(), nullable=True),
    sa.Column('condition', sa.String(length=24), nullable=False),
    sa.Column('value', sa.Float(), nullable=True),
    sa.Column('ma', sa.String(length=8), nullable=True),
    sa.Column('channels', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('priority', sa.String(length=8), server_default='normal', nullable=False),
    sa.Column('last_fired_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['screen_id'], ['saved_screens.id'], name=op.f('fk_alert_rules_screen_id_saved_screens'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['ticker_id'], ['tickers.id'], name=op.f('fk_alert_rules_ticker_id_tickers'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_alert_rules_user_id_users'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['watchlist_id'], ['watchlists.id'], name=op.f('fk_alert_rules_watchlist_id_watchlists'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_alert_rules'))
    )
    op.create_index(op.f('ix_alert_rules_user_id'), 'alert_rules', ['user_id'], unique=False)
    op.create_table('intraday_bars',
    sa.Column('ticker_id', sa.Integer(), nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('open', sa.Float(), nullable=False),
    sa.Column('high', sa.Float(), nullable=False),
    sa.Column('low', sa.Float(), nullable=False),
    sa.Column('close', sa.Float(), nullable=False),
    sa.Column('volume', sa.BigInteger(), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.ForeignKeyConstraint(['ticker_id'], ['tickers.id'], name=op.f('fk_intraday_bars_ticker_id_tickers'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('ticker_id', 'ts', name=op.f('pk_intraday_bars'))
    )
    op.create_table('holdings',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('ticker_id', sa.Integer(), nullable=False),
    sa.Column('setup_id', sa.BigInteger(), nullable=True),
    sa.Column('opened_on', sa.Date(), nullable=False),
    sa.Column('entry_price', sa.Float(), nullable=False),
    sa.Column('shares', sa.Integer(), nullable=False),
    sa.Column('initial_stop', sa.Float(), nullable=False),
    sa.Column('stop', sa.Float(), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('closed_on', sa.Date(), nullable=True),
    sa.Column('exit_price', sa.Float(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['setup_id'], ['setups.id'], name=op.f('fk_holdings_setup_id_setups'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['ticker_id'], ['tickers.id'], name=op.f('fk_holdings_ticker_id_tickers'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_holdings_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_holdings'))
    )
    op.create_index(op.f('ix_holdings_user_id'), 'holdings', ['user_id'], unique=False)
    op.create_table('alerts',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('session_date', sa.Date(), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('priority', sa.String(length=8), nullable=False),
    sa.Column('ticker_id', sa.Integer(), nullable=True),
    sa.Column('symbol', sa.String(length=16), nullable=True),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('signal_id', sa.BigInteger(), nullable=True),
    sa.Column('rule_id', sa.BigInteger(), nullable=True),
    sa.Column('holding_id', sa.BigInteger(), nullable=True),
    sa.Column('dedupe_key', sa.String(length=160), nullable=False),
    sa.Column('delivery', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('digested_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['holding_id'], ['holdings.id'], name=op.f('fk_alerts_holding_id_holdings'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['rule_id'], ['alert_rules.id'], name=op.f('fk_alerts_rule_id_alert_rules'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['signal_id'], ['signals.id'], name=op.f('fk_alerts_signal_id_signals'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['ticker_id'], ['tickers.id'], name=op.f('fk_alerts_ticker_id_tickers'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_alerts_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_alerts'))
    )
    op.create_index('ix_alerts_dedupe', 'alerts', ['user_id', 'dedupe_key', 'created_at'], unique=False)
    op.create_index('ix_alerts_user_created', 'alerts', ['user_id', sa.literal_column('created_at DESC')], unique=False)
    # Minute bars: daily chunks, dropped after 30 days (only watched names are stored).
    op.execute("SELECT create_hypertable('intraday_bars', by_range('ts', INTERVAL '1 day'))")
    op.execute("SELECT add_retention_policy('intraday_bars', INTERVAL '30 days')")


def downgrade() -> None:
    op.drop_index('ix_alerts_user_created', table_name='alerts')
    op.drop_index('ix_alerts_dedupe', table_name='alerts')
    op.drop_table('alerts')
    op.drop_index(op.f('ix_holdings_user_id'), table_name='holdings')
    op.drop_table('holdings')
    op.drop_table('intraday_bars')
    op.drop_index(op.f('ix_alert_rules_user_id'), table_name='alert_rules')
    op.drop_table('alert_rules')
    op.drop_table('volume_profiles')
