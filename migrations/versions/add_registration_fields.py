"""add registration fields

Revision ID: add_registration_fields
Revises: 1922f88b5aec
Create Date: 2025-01-15 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_registration_fields'
down_revision = '1922f88b5aec'
branch_labels = None
depends_on = None


def upgrade():
    # Add new fields to event_registration table
    with op.batch_alter_table('event_registration', schema=None) as batch_op:
        batch_op.add_column(sa.Column('department', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('college', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('year', sa.String(length=10), nullable=True))


def downgrade():
    # Remove fields if we need to rollback
    with op.batch_alter_table('event_registration', schema=None) as batch_op:
        batch_op.drop_column('year')
        batch_op.drop_column('college')
        batch_op.drop_column('department')
