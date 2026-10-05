"""Phase 5: watchlists (ordered, a note per stock), saved screener screens, a note per stock,
and trigram indexes on ticker symbols and names for search (pg_trgm).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-05 20:19:58.312845
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0007'
down_revision: str | Sequence[str] | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
    op.create_table('saved_screens',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('filters', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('sort', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('columns', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_saved_screens_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_saved_screens')),
    sa.UniqueConstraint('user_id', 'name', name=op.f('uq_saved_screens_user_id'))
    )
    op.create_index(op.f('ix_saved_screens_user_id'), 'saved_screens', ['user_id'], unique=False)
    op.create_table('watchlists',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('position', sa.Integer(), server_default='0', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_watchlists_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_watchlists')),
    sa.UniqueConstraint('user_id', 'name', name=op.f('uq_watchlists_user_id'))
    )
    op.create_index(op.f('ix_watchlists_user_id'), 'watchlists', ['user_id'], unique=False)
    op.create_table('stock_notes',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('ticker_id', sa.Integer(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['ticker_id'], ['tickers.id'], name=op.f('fk_stock_notes_ticker_id_tickers'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_stock_notes_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'ticker_id', name=op.f('pk_stock_notes'))
    )
    op.create_table('watchlist_items',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('watchlist_id', sa.BigInteger(), nullable=False),
    sa.Column('ticker_id', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), server_default='0', nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('added_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['ticker_id'], ['tickers.id'], name=op.f('fk_watchlist_items_ticker_id_tickers'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['watchlist_id'], ['watchlists.id'], name=op.f('fk_watchlist_items_watchlist_id_watchlists'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_watchlist_items')),
    sa.UniqueConstraint('watchlist_id', 'ticker_id', name=op.f('uq_watchlist_items_watchlist_id'))
    )
    op.create_index(op.f('ix_watchlist_items_watchlist_id'), 'watchlist_items', ['watchlist_id'], unique=False)
    op.create_index('ix_tickers_name_trgm', 'tickers', ['name'], unique=False, postgresql_using='gin', postgresql_ops={'name': 'gin_trgm_ops'})
    op.create_index('ix_tickers_symbol_trgm', 'tickers', ['symbol'], unique=False, postgresql_using='gin', postgresql_ops={'symbol': 'gin_trgm_ops'})


def downgrade() -> None:
    op.drop_index('ix_tickers_symbol_trgm', table_name='tickers', postgresql_using='gin', postgresql_ops={'symbol': 'gin_trgm_ops'})
    op.drop_index('ix_tickers_name_trgm', table_name='tickers', postgresql_using='gin', postgresql_ops={'name': 'gin_trgm_ops'})
    op.drop_index(op.f('ix_watchlist_items_watchlist_id'), table_name='watchlist_items')
    op.drop_table('watchlist_items')
    op.drop_table('stock_notes')
    op.drop_index(op.f('ix_watchlists_user_id'), table_name='watchlists')
    op.drop_table('watchlists')
    op.drop_index(op.f('ix_saved_screens_user_id'), table_name='saved_screens')
    op.drop_table('saved_screens')
