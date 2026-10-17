"""deputation circulars

Revision ID: a6c1d94e2b70
Revises: 8d2f6b1e4c90
Create Date: 2026-10-06 18:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a6c1d94e2b70'
down_revision: Union[str, Sequence[str], None] = '8d2f6b1e4c90'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('deputation_circulars',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('raw_document_id', sa.Uuid(), nullable=False),
    sa.Column('title', sa.Text(), nullable=True),
    sa.Column('parser_version', sa.String(length=40), nullable=False),
    sa.Column('mode', sa.String(length=100), nullable=True),
    sa.Column('closing_kind', sa.String(length=30), nullable=True),
    sa.Column('closing_date', sa.Date(), nullable=True),
    sa.Column('closing_days', sa.Integer(), nullable=True),
    sa.Column('post_count', sa.Integer(), nullable=False),
    sa.Column('terms', sa.Text(), nullable=False),
    sa.Column('parsed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['raw_document_id'], ['raw_documents.id'], name='fk_deputation_circulars_raw_document_id'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('deputation_circulars', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_deputation_circulars_raw_document_id'), ['raw_document_id'], unique=True)
        batch_op.create_index(batch_op.f('ix_deputation_circulars_closing_date'), ['closing_date'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('deputation_circulars', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_deputation_circulars_closing_date'))
        batch_op.drop_index(batch_op.f('ix_deputation_circulars_raw_document_id'))

    op.drop_table('deputation_circulars')
