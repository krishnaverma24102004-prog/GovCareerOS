"""ex-servicemen and Agniveer profile fields

Revision ID: e5a1c7d93b20
Revises: d616c69d4062
Create Date: 2026-10-06 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5a1c7d93b20'
down_revision: Union[str, Sequence[str], None] = 'd616c69d4062'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # All nullable: not stated means the ESM section reports CANNOT_DECIDE, never a guess
    with op.batch_alter_table('personal_info', schema=None) as batch_op:
        batch_op.add_column(sa.Column('esm_service_branch', sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column('esm_enrolment_date', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('esm_release_date', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('esm_release_type', sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column('esm_rank', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('esm_civil_post_secured', sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column('esm_civil_post_government', sa.String(length=10), nullable=True))
        batch_op.add_column(sa.Column('esm_reservation_used', sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column('esm_civil_post_group', sa.String(length=1), nullable=True))
        batch_op.add_column(sa.Column('esm_undertaking_given', sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column('is_agniveer', sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('personal_info', schema=None) as batch_op:
        batch_op.drop_column('is_agniveer')
        batch_op.drop_column('esm_undertaking_given')
        batch_op.drop_column('esm_civil_post_group')
        batch_op.drop_column('esm_reservation_used')
        batch_op.drop_column('esm_civil_post_government')
        batch_op.drop_column('esm_civil_post_secured')
        batch_op.drop_column('esm_rank')
        batch_op.drop_column('esm_release_type')
        batch_op.drop_column('esm_release_date')
        batch_op.drop_column('esm_enrolment_date')
        batch_op.drop_column('esm_service_branch')
