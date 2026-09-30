"""vacancy recruitment mode and state government employee flag

Revision ID: 5b2d9e7c4a10
Revises: 33dc3ded2525
Create Date: 2026-09-30 14:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5b2d9e7c4a10'
down_revision: Union[str, Sequence[str], None] = '33dc3ded2525'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing vacancies stay UNKNOWN until a re-parse or re-review reads the mode from their document
    with op.batch_alter_table('vacancies', schema=None) as batch_op:
        batch_op.add_column(sa.Column('recruitment_mode', sa.String(length=20), server_default='UNKNOWN', nullable=False))
        batch_op.create_index(batch_op.f('ix_vacancies_recruitment_mode'), ['recruitment_mode'], unique=False)
    with op.batch_alter_table('personal_info', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_state_govt_employee', sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('personal_info', schema=None) as batch_op:
        batch_op.drop_column('is_state_govt_employee')
    with op.batch_alter_table('vacancies', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_vacancies_recruitment_mode'))
        batch_op.drop_column('recruitment_mode')
