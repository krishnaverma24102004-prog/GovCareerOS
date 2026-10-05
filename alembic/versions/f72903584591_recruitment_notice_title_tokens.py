"""recruitment notice title tokens

Revision ID: f72903584591
Revises: a7c3e91f2d64
Create Date: 2026-10-05 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from govcareeros.backend.parsing.title_tokens import normalise, token_counts


# revision identifiers, used by Alembic.
revision: str = 'f72903584591'
down_revision: Union[str, Sequence[str], None] = 'a7c3e91f2d64'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The public notice check looks up notices by title word here instead of loading every notice
    tokens = op.create_table('recruitment_notice_title_tokens',
    sa.Column('notice_id', sa.Uuid(), nullable=False),
    sa.Column('token', sa.Text(), nullable=False),
    sa.Column('occurrences', sa.Integer(), nullable=False),
    sa.Column('title_tokens', sa.Integer(), nullable=False),
    sa.Column('title_chars', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['notice_id'], ['recruitment_notices.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('notice_id', 'token')
    )
    with op.batch_alter_table('recruitment_notice_title_tokens', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_recruitment_notice_title_tokens_title_chars'), ['title_chars'], unique=False)
        batch_op.create_index(batch_op.f('ix_recruitment_notice_title_tokens_token'), ['token'], unique=False)

    # Existing notices; new ones are indexed by the before_flush hook in backend/models/notice.py
    notices = sa.table('recruitment_notices', sa.column('id', sa.Uuid()), sa.column('title', sa.Text()))
    rows = []
    for notice_id, title in op.get_bind().execute(sa.select(notices.c.id, notices.c.title)):
        counts = token_counts(title or "")
        total, chars = sum(counts.values()), len(normalise(title or ""))
        rows += [dict(notice_id=notice_id, token=t, occurrences=n, title_tokens=total, title_chars=chars)
                 for t, n in counts.items()]
    if rows:
        op.bulk_insert(tokens, rows)


def downgrade() -> None:
    with op.batch_alter_table('recruitment_notice_title_tokens', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_recruitment_notice_title_tokens_token'))
        batch_op.drop_index(batch_op.f('ix_recruitment_notice_title_tokens_title_chars'))

    op.drop_table('recruitment_notice_title_tokens')
