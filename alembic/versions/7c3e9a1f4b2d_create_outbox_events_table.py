"""create outbox_events table

Revision ID: 7c3e9a1f4b2d
Revises: b45bac460dfd
Create Date: 2026-11-06 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '7c3e9a1f4b2d'
down_revision: Union[str, Sequence[str], None] = 'b45bac460dfd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('outbox_events',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('aggregate_id', sa.UUID(), nullable=False),
    sa.Column('event_type', sa.String(length=64), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_outbox_events_unpublished', 'outbox_events', ['created_at'], unique=False, postgresql_where=sa.text('published_at IS NULL'))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_outbox_events_unpublished', table_name='outbox_events')
    op.drop_table('outbox_events')
