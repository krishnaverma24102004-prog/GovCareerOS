"""exam change alerts and exam applications

Revision ID: c5d82a4e17b3
Revises: a7c3e91f2d64
Create Date: 2026-10-05 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5d82a4e17b3'
down_revision: Union[str, Sequence[str], None] = 'a7c3e91f2d64'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # An alert about an official change to an exam names the exam and the ExamVersion it reports
    with op.batch_alter_table('user_alerts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('exam_id', sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column('exam_version_id', sa.Uuid(), nullable=True))
        batch_op.create_index(batch_op.f('ix_user_alerts_exam_id'), ['exam_id'], unique=False)
        batch_op.create_foreign_key('fk_user_alerts_exam_id', 'exams', ['exam_id'], ['id'])
        batch_op.create_foreign_key('fk_user_alerts_exam_version_id', 'exam_versions', ['exam_version_id'], ['id'])

    # A tracked application may point at a stored exam (exam_ref keeps the exam's name, so ck_applications_target holds)
    with op.batch_alter_table('applications', schema=None) as batch_op:
        batch_op.add_column(sa.Column('exam_id', sa.Uuid(), nullable=True))
        batch_op.create_index(batch_op.f('ix_applications_exam_id'), ['exam_id'], unique=False)
        batch_op.create_foreign_key('fk_applications_exam_id', 'exams', ['exam_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('applications', schema=None) as batch_op:
        batch_op.drop_constraint('fk_applications_exam_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_applications_exam_id'))
        batch_op.drop_column('exam_id')

    with op.batch_alter_table('user_alerts', schema=None) as batch_op:
        batch_op.drop_constraint('fk_user_alerts_exam_version_id', type_='foreignkey')
        batch_op.drop_constraint('fk_user_alerts_exam_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_user_alerts_exam_id'))
        batch_op.drop_column('exam_version_id')
        batch_op.drop_column('exam_id')
