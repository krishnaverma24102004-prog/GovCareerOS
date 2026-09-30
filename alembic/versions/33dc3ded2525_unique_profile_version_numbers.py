"""unique profile version numbers

Revision ID: 33dc3ded2525
Revises: 3c8e1f4a9b27
Create Date: 2026-09-30 13:20:40.920951

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '33dc3ded2525'
down_revision: Union[str, Sequence[str], None] = '3c8e1f4a9b27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Concurrent saves could already have written duplicate numbers. Only the later rows of a collision are
    # renumbered (after the user's highest number); every other version keeps the number users have seen.
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT id, user_id, version_number FROM profile_versions ORDER BY user_id, version_number, created_at, id")).all()
    highest, seen = {}, set()
    for _, user_id, number in rows:
        highest[user_id] = max(highest.get(user_id, 0), number)
    for id_, user_id, number in rows:
        if (user_id, number) in seen:
            highest[user_id] += 1
            bind.execute(sa.text("UPDATE profile_versions SET version_number = :n WHERE id = :id"), {"n": highest[user_id], "id": id_})
        else:
            seen.add((user_id, number))
    with op.batch_alter_table('profile_versions', schema=None) as batch_op:
        batch_op.create_unique_constraint('uq_profile_versions_user_version', ['user_id', 'version_number'])


def downgrade() -> None:
    with op.batch_alter_table('profile_versions', schema=None) as batch_op:
        batch_op.drop_constraint('uq_profile_versions_user_version', type_='unique')
