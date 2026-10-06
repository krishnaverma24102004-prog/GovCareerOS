"""application requirement parses (parser warnings per notice document)

Revision ID: 969b734aaf03
Revises: 497e36d5ddc3
Create Date: 2026-10-06 18:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '969b734aaf03'
down_revision: Union[str, Sequence[str], None] = '497e36d5ddc3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Documents ingested earlier have no row (no warnings shown) until their application instructions are parsed again
    op.create_table('application_requirement_parses',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('raw_document_id', sa.Uuid(), nullable=False),
    sa.Column('parser_version', sa.String(length=100), nullable=False),
    sa.Column('warnings', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['raw_document_id'], ['raw_documents.id'], name='fk_application_requirement_parses_raw_document_id'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('raw_document_id', name='uq_application_requirement_parses_raw_document_id')
    )


def downgrade() -> None:
    op.drop_table('application_requirement_parses')
