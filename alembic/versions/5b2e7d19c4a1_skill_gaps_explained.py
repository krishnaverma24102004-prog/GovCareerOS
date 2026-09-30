"""skill gaps explained: pinned to profile version + eligibility result, cited, with next step

Revision ID: 5b2e7d19c4a1
Revises: baa1aac507db
Create Date: 2026-09-30 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5b2e7d19c4a1'
down_revision: Union[str, Sequence[str], None] = 'baa1aac507db'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('skill_gaps', schema=None) as batch_op:
        batch_op.add_column(sa.Column('profile_version_id', sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column('eligibility_result_id', sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column('requirement_id', sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column('engine_version', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('reason_code', sa.String(length=60), nullable=True))
        batch_op.add_column(sa.Column('blocking', sa.Boolean(), server_default='false', nullable=False))
        batch_op.add_column(sa.Column('requirement_quote', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('requirement_page', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('profile_has', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('profile_field', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('next_step', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('due_date', sa.Date(), nullable=True))
        batch_op.create_index(batch_op.f('ix_skill_gaps_profile_version_id'), ['profile_version_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_skill_gaps_eligibility_result_id'), ['eligibility_result_id'], unique=False)
        batch_op.create_foreign_key('fk_skill_gaps_profile_version_id', 'profile_versions', ['profile_version_id'], ['id'])
        batch_op.create_foreign_key('fk_skill_gaps_eligibility_result_id', 'eligibility_results', ['eligibility_result_id'], ['id'])
        batch_op.create_foreign_key('fk_skill_gaps_requirement_id', 'vacancy_requirements', ['requirement_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('skill_gaps', schema=None) as batch_op:
        batch_op.drop_constraint('fk_skill_gaps_requirement_id', type_='foreignkey')
        batch_op.drop_constraint('fk_skill_gaps_eligibility_result_id', type_='foreignkey')
        batch_op.drop_constraint('fk_skill_gaps_profile_version_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_skill_gaps_eligibility_result_id'))
        batch_op.drop_index(batch_op.f('ix_skill_gaps_profile_version_id'))
        for column in ('due_date', 'next_step', 'profile_field', 'profile_has', 'requirement_page', 'requirement_quote',
                       'blocking', 'reason_code', 'engine_version', 'requirement_id', 'eligibility_result_id', 'profile_version_id'):
            batch_op.drop_column(column)
