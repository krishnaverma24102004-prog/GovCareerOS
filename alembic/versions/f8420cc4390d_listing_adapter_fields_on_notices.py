"""listing adapter fields on notices

Revision ID: f8420cc4390d
Revises: a7c3e91f2d64
Create Date: 2026-10-05 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f8420cc4390d'
down_revision: Union[str, Sequence[str], None] = 'a7c3e91f2d64'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Filled by source discovery from the source's listing adapter; NULL where the listing states nothing
    with op.batch_alter_table('recruitment_notices', schema=None) as batch_op:
        batch_op.add_column(sa.Column('published_on', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('last_date', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('advertisement_number', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('post_names', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('kind_hint', sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column('listing_evidence', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('listing_row_key', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('recruitment_notices', schema=None) as batch_op:
        batch_op.drop_column('listing_row_key')
        batch_op.drop_column('listing_evidence')
        batch_op.drop_column('kind_hint')
        batch_op.drop_column('post_names')
        batch_op.drop_column('advertisement_number')
        batch_op.drop_column('last_date')
        batch_op.drop_column('published_on')
