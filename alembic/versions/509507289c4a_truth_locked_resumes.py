"""truth locked resumes

Revision ID: 509507289c4a
Revises: 40a4330a751f
Create Date: 2026-09-30 14:15:55.295154

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '509507289c4a'
down_revision: Union[str, Sequence[str], None] = '40a4330a751f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Resumes are built from facts; every claim references its profile_facts row (and, for "Meets requirement",
    # the vacancy_requirements row it quotes)
    op.create_table('resumes',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('profile_version_id', sa.Uuid(), nullable=False),
    sa.Column('vacancy_id', sa.Uuid(), nullable=True),
    sa.Column('eligibility_result_id', sa.Uuid(), nullable=True),
    sa.Column('builder_version', sa.String(length=50), nullable=False),
    sa.Column('html_path', sa.String(length=1000), nullable=True),
    sa.Column('docx_path', sa.String(length=1000), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['eligibility_result_id'], ['eligibility_results.id'], name='fk_resumes_eligibility_result_id'),
    sa.ForeignKeyConstraint(['profile_version_id'], ['profile_versions.id'], name='fk_resumes_profile_version_id'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_resumes_user_id'),
    sa.ForeignKeyConstraint(['vacancy_id'], ['vacancies.id'], name='fk_resumes_vacancy_id'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('resumes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_resumes_profile_version_id'), ['profile_version_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_resumes_user_id'), ['user_id'], unique=False)

    op.create_table('resume_claims',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('resume_id', sa.Uuid(), nullable=False),
    sa.Column('fact_id', sa.Uuid(), nullable=False),
    sa.Column('section', sa.String(length=50), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('item_ref', sa.String(length=100), nullable=False),
    sa.Column('field', sa.String(length=100), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('verified', sa.Boolean(), nullable=False),
    sa.Column('requirement_id', sa.Uuid(), nullable=True),
    sa.Column('requirement_quote', sa.Text(), nullable=True),
    sa.Column('requirement_page', sa.Integer(), nullable=True),
    sa.Column('requirement_outcome', sa.String(length=50), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['fact_id'], ['profile_facts.id'], name='fk_resume_claims_fact_id'),
    sa.ForeignKeyConstraint(['requirement_id'], ['vacancy_requirements.id'], name='fk_resume_claims_requirement_id'),
    sa.ForeignKeyConstraint(['resume_id'], ['resumes.id'], name='fk_resume_claims_resume_id'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('resume_claims', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_resume_claims_fact_id'), ['fact_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_resume_claims_resume_id'), ['resume_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('resume_claims', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_resume_claims_resume_id'))
        batch_op.drop_index(batch_op.f('ix_resume_claims_fact_id'))

    op.drop_table('resume_claims')
    with op.batch_alter_table('resumes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_resumes_user_id'))
        batch_op.drop_index(batch_op.f('ix_resumes_profile_version_id'))

    op.drop_table('resumes')
