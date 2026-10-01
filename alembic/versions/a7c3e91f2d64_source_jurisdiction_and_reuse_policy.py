"""source jurisdiction and reuse policy

Revision ID: a7c3e91f2d64
Revises: 4742ebe6b1bf
Create Date: 2026-10-01 16:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c3e91f2d64'
down_revision: Union[str, Sequence[str], None] = '4742ebe6b1bf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # jurisdiction is filled by sync_registry from the registry; reuse policy is set by a reviewer (UNKNOWN until then)
    with op.batch_alter_table('government_sources', schema=None) as batch_op:
        batch_op.add_column(sa.Column('jurisdiction', sa.String(length=10), nullable=True))
        batch_op.add_column(sa.Column('copyright_policy_url', sa.String(length=1000), nullable=True))
        batch_op.add_column(sa.Column('reuse_level', sa.String(length=20), server_default='UNKNOWN', nullable=False))
        batch_op.add_column(sa.Column('reuse_checked_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index(batch_op.f('ix_government_sources_jurisdiction'), ['jurisdiction'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('government_sources', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_government_sources_jurisdiction'))
        batch_op.drop_column('reuse_checked_at')
        batch_op.drop_column('reuse_level')
        batch_op.drop_column('copyright_policy_url')
        batch_op.drop_column('jurisdiction')
