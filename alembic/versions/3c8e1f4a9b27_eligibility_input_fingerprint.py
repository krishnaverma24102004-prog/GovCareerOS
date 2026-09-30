"""eligibility input fingerprint

Revision ID: 3c8e1f4a9b27
Revises: 987727a5c41d
Create Date: 2026-09-30 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3c8e1f4a9b27'
down_revision: Union[str, Sequence[str], None] = '987727a5c41d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Existing rows stay NULL: the radar treats them as stale and decides again once
    with op.batch_alter_table('eligibility_results', schema=None) as batch_op:
        batch_op.add_column(sa.Column('input_fingerprint', sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('eligibility_results', schema=None) as batch_op:
        batch_op.drop_column('input_fingerprint')
