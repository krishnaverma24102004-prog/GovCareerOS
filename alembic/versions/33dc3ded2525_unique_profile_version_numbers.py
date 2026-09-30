"""unique profile version numbers

Revision ID: 33dc3ded2525
Revises: 987727a5c41d
Create Date: 2026-09-30 13:20:40.920951

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '33dc3ded2525'
down_revision: Union[str, Sequence[str], None] = '987727a5c41d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Concurrent saves could already have written duplicate numbers; renumber each user's versions
    # 1..n in (version_number, created_at, id) order so the constraint can be added. Rows keep their ids.
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT id, user_id, version_number FROM profile_versions ORDER BY user_id, version_number, created_at, id")).all()
    counters = {}
    for id_, user_id, number in rows:
        counters[user_id] = counters.get(user_id, 0) + 1
        if counters[user_id] != number:
            bind.execute(sa.text("UPDATE profile_versions SET version_number = :n WHERE id = :id"), {"n": counters[user_id], "id": id_})
    with op.batch_alter_table('profile_versions', schema=None) as batch_op:
        batch_op.create_unique_constraint('uq_profile_versions_user_version', ['user_id', 'version_number'])


def downgrade() -> None:
    with op.batch_alter_table('profile_versions', schema=None) as batch_op:
        batch_op.drop_constraint('uq_profile_versions_user_version', type_='unique')
