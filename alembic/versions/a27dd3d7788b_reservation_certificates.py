"""reservation certificates held by the candidate (profile section)

Revision ID: a27dd3d7788b
Revises: a7c3e91f2d64
Create Date: 2026-10-05 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a27dd3d7788b'
down_revision: Union[str, Sequence[str], None] = 'a7c3e91f2d64'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # One row per certificate per immutable profile version; verification lives on its profile Fact
    op.create_table('reservation_certificates',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('profile_version_id', sa.Uuid(), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('issue_date', sa.DateTime(), nullable=True),
    sa.Column('financial_year', sa.String(length=9), nullable=True),
    sa.Column('valid_until', sa.DateTime(), nullable=True),
    sa.Column('issuing_authority', sa.String(length=255), nullable=True),
    sa.Column('central_format', sa.Boolean(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['profile_version_id'], ['profile_versions.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('reservation_certificates', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_reservation_certificates_profile_version_id'), ['profile_version_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('reservation_certificates', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_reservation_certificates_profile_version_id'))
    op.drop_table('reservation_certificates')
