"""Phase 8: each setup's verdict (buy / get ready / not yet / don't buy, with its checks, order
and plain-English summary) and its breakout-watch readiness.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-20 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0010'
down_revision: str | Sequence[str] | None = '0009'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('setups', sa.Column('action', sa.String(length=16), nullable=True))
    op.add_column('setups', sa.Column('verdict', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('setups', sa.Column('watch_score', sa.Float(), nullable=True))
    op.add_column('setups', sa.Column('watch', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('setups', sa.Column('watch_since', sa.Date(), nullable=True))
    op.create_index('ix_setups_action', 'setups', ['action', 'active'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_setups_action', table_name='setups')
    op.drop_column('setups', 'watch_since')
    op.drop_column('setups', 'watch')
    op.drop_column('setups', 'watch_score')
    op.drop_column('setups', 'verdict')
    op.drop_column('setups', 'action')
