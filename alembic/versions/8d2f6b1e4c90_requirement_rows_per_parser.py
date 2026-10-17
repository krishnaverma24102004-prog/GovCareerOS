"""requirement rows per parser

Revision ID: 8d2f6b1e4c90
Revises: 497e36d5ddc3
Create Date: 2026-10-06 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8d2f6b1e4c90'
down_revision: Union[str, Sequence[str], None] = '497e36d5ddc3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A newer deterministic parse no longer deletes reviewer-approved LLM rows of the same document, so both can
    # state the same (req_type, code)
    with op.batch_alter_table('application_requirements', schema=None) as batch_op:
        batch_op.drop_constraint('uq_application_requirements_doc_type_code', type_='unique')
        batch_op.create_unique_constraint('uq_application_requirements_doc_type_code_parser',
                                          ['raw_document_id', 'req_type', 'code', 'parser_version'])


def downgrade() -> None:
    # The old key allows one row per (document, type, code): the reviewer-approved row is human work and stays;
    # the deterministic duplicate is dropped (a re-parse of the document brings it back)
    op.execute(sa.text(
        "DELETE FROM application_requirements WHERE parser_version NOT LIKE 'llm:%' AND EXISTS ("
        "SELECT 1 FROM application_requirements o WHERE o.raw_document_id = application_requirements.raw_document_id "
        "AND o.req_type = application_requirements.req_type AND o.code = application_requirements.code "
        "AND o.parser_version LIKE 'llm:%')"))
    with op.batch_alter_table('application_requirements', schema=None) as batch_op:
        batch_op.drop_constraint('uq_application_requirements_doc_type_code_parser', type_='unique')
        batch_op.create_unique_constraint('uq_application_requirements_doc_type_code', ['raw_document_id', 'req_type', 'code'])
