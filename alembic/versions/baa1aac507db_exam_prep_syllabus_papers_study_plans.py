"""exam prep syllabus papers study plans

Revision ID: baa1aac507db
Revises: cb4fe9abe6c8
Create Date: 2026-09-30 14:03:47.215533

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'baa1aac507db'
down_revision: Union[str, Sequence[str], None] = 'cb4fe9abe6c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:

    op.create_table('question_papers',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('exam_code', sa.String(length=40), nullable=False),
    sa.Column('exam_name', sa.Text(), nullable=False),
    sa.Column('stage', sa.String(length=20), nullable=True),
    sa.Column('year', sa.Integer(), nullable=False),
    sa.Column('category', sa.String(length=200), nullable=True),
    sa.Column('paper_name', sa.Text(), nullable=False),
    sa.Column('official_url', sa.Text(), nullable=False),
    sa.Column('url_hash', sa.String(length=64), nullable=False),
    sa.Column('size_label', sa.String(length=20), nullable=True),
    sa.Column('listing_url', sa.Text(), nullable=False),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('raw_document_id', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['raw_document_id'], ['raw_documents.id'], name='fk_question_papers_raw_document_id'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('url_hash', name='uq_question_papers_url_hash')
    )
    with op.batch_alter_table('question_papers', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_question_papers_exam_code'), ['exam_code'], unique=False)
        batch_op.create_index(batch_op.f('ix_question_papers_year'), ['year'], unique=False)

    op.create_table('study_plans',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('exam_code', sa.String(length=40), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('target_date', sa.Date(), nullable=False),
    sa.Column('weekly_hours', sa.Float(), nullable=False),
    sa.Column('total_weeks', sa.Integer(), nullable=False),
    sa.Column('revision_weeks', sa.Integer(), nullable=False),
    sa.Column('sections', sa.Text(), nullable=True),
    sa.Column('syllabus_raw_document_id', sa.Uuid(), nullable=False),
    sa.Column('generator_version', sa.String(length=50), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['syllabus_raw_document_id'], ['raw_documents.id'], name='fk_study_plans_syllabus_raw_document_id'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_study_plans_user_id'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('study_plans', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_study_plans_user_id'), ['user_id'], unique=False)

    op.create_table('syllabus_entries',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('exam_code', sa.String(length=40), nullable=False),
    sa.Column('section', sa.String(length=200), nullable=False),
    sa.Column('section_order', sa.Integer(), nullable=False),
    sa.Column('section_note', sa.Text(), nullable=True),
    sa.Column('part', sa.String(length=200), nullable=True),
    sa.Column('topic', sa.Text(), nullable=False),
    sa.Column('topic_order', sa.Integer(), nullable=False),
    sa.Column('subtopic', sa.Text(), nullable=False),
    sa.Column('subtopic_order', sa.Integer(), nullable=False),
    sa.Column('raw_document_id', sa.Uuid(), nullable=False),
    sa.Column('page', sa.Integer(), nullable=False),
    sa.Column('parser_version', sa.String(length=50), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['raw_document_id'], ['raw_documents.id'], name='fk_syllabus_entries_raw_document_id'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('raw_document_id', 'exam_code', 'section', 'topic_order', 'subtopic_order', name='uq_syllabus_entries_position')
    )
    with op.batch_alter_table('syllabus_entries', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_syllabus_entries_exam_code'), ['exam_code'], unique=False)
        batch_op.create_index(batch_op.f('ix_syllabus_entries_raw_document_id'), ['raw_document_id'], unique=False)

    op.create_table('study_plan_items',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('plan_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('planned_week', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('section', sa.String(length=200), nullable=True),
    sa.Column('topic', sa.Text(), nullable=False),
    sa.Column('planned_hours', sa.Float(), nullable=False),
    sa.Column('syllabus_page', sa.Integer(), nullable=True),
    sa.Column('question_paper_id', sa.Uuid(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['plan_id'], ['study_plans.id'], name='fk_study_plan_items_plan_id'),
    sa.ForeignKeyConstraint(['question_paper_id'], ['question_papers.id'], name='fk_study_plan_items_question_paper_id'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('study_plan_items', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_study_plan_items_plan_id'), ['plan_id'], unique=False)




def downgrade() -> None:

    with op.batch_alter_table('study_plan_items', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_study_plan_items_plan_id'))

    op.drop_table('study_plan_items')
    with op.batch_alter_table('syllabus_entries', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_syllabus_entries_raw_document_id'))
        batch_op.drop_index(batch_op.f('ix_syllabus_entries_exam_code'))

    op.drop_table('syllabus_entries')
    with op.batch_alter_table('study_plans', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_study_plans_user_id'))

    op.drop_table('study_plans')
    with op.batch_alter_table('question_papers', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_question_papers_year'))
        batch_op.drop_index(batch_op.f('ix_question_papers_exam_code'))

    op.drop_table('question_papers')

