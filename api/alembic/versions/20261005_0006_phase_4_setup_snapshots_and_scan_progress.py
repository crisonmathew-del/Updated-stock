"""Phase 4: a snapshot of each setup before the session it was last evaluated on and the
session that recorded each transition (so a session can be re-run), and the forward-only scan
stages' progress.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-05 15:57:44.064083
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0006'
down_revision: str | Sequence[str] | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('scan_progress',
    sa.Column('stage', sa.String(length=32), nullable=False),
    sa.Column('through', sa.Date(), nullable=False),
    sa.Column('stats', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('stage', name=op.f('pk_scan_progress'))
    )
    op.add_column('setup_transitions', sa.Column('recorded_on', sa.Date(), nullable=True))
    op.execute('UPDATE setup_transitions SET recorded_on = date')
    op.alter_column('setup_transitions', 'recorded_on', nullable=False)
    op.create_index(op.f('ix_setup_transitions_recorded_on'), 'setup_transitions', ['recorded_on'], unique=False)
    op.add_column('setups', sa.Column('previous', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('setups', 'previous')
    op.drop_index(op.f('ix_setup_transitions_recorded_on'), table_name='setup_transitions')
    op.drop_column('setup_transitions', 'recorded_on')
    op.drop_table('scan_progress')
