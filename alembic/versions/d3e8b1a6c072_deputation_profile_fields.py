"""serving-officer profile fields for deputation

Revision ID: d3e8b1a6c072
Revises: a7c3e91f2d64
Create Date: 2026-10-05 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd3e8b1a6c072'
down_revision: Union[str, Sequence[str], None] = 'a7c3e91f2d64'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # All nullable: not stated means the deputation checks report UNKNOWN / reviewer, never a guess
    with op.batch_alter_table('personal_info', schema=None) as batch_op:
        batch_op.add_column(sa.Column('current_pay_level', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('current_level_regular_since', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('organisation_cadre', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('last_deputation_end', sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('personal_info', schema=None) as batch_op:
        batch_op.drop_column('last_deputation_end')
        batch_op.drop_column('organisation_cadre')
        batch_op.drop_column('current_level_regular_since')
        batch_op.drop_column('current_pay_level')
