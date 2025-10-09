"""add participating events field

Revision ID: add_participating_events
Revises: add_registration_fields
Create Date: 2025-01-15 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_participating_events'
down_revision = 'add_registration_fields'
branch_labels = None
depends_on = None


def upgrade():
    # Add participating_events field to event_registration table
    with op.batch_alter_table('event_registration', schema=None) as batch_op:
        batch_op.add_column(sa.Column('participating_events', sa.Text(), nullable=True))


def downgrade():
    # Remove participating_events field if we need to rollback
    with op.batch_alter_table('event_registration', schema=None) as batch_op:
        batch_op.drop_column('participating_events')
