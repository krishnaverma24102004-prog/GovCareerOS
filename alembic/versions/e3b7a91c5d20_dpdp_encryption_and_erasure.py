"""DPDP: field-level encryption of category/caste and disability columns; erasure and legal-hold columns on users

Revision ID: e3b7a91c5d20
Revises: e77a7e08ad6e
Create Date: 2026-10-06 15:00:00

Existing plaintext values are read with their old types, the columns become Text, and the values are written back
as ciphertext (backend/privacy/crypto.py) with FIELD_ENCRYPTION_KEY from the environment / .env. Text columns that
quote those values (profile claims, eligibility evidence, skill gaps, alert bodies) keep their type and have their
existing values rewritten as ciphertext, in batches. If any such value exists and no key is configured the migration
stops before changing anything. Downgrade decrypts them back.
"""
from typing import Dict, List, Sequence, Tuple, Union

from alembic import op
import sqlalchemy as sa

from govcareeros.backend.privacy.crypto import decrypt_value, encrypt_value, is_ciphertext, require_key


# revision identifiers, used by Alembic.
revision: str = 'e3b7a91c5d20'
down_revision: Union[str, Sequence[str], None] = 'e77a7e08ad6e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# table -> [(column, plaintext type, its PostgreSQL cast name)]
ENCRYPTED: Dict[str, List[Tuple[str, sa.types.TypeEngine, str]]] = {
    'personal_info': [('category', sa.String(length=100), 'varchar(100)'), ('is_pwbd', sa.Boolean(), 'boolean'),
                      ('disability_percentage', sa.Integer(), 'integer'), ('disability_types', sa.String(length=100), 'varchar(100)')],
    'reservation_certificates': [('kind', sa.String(length=20), 'varchar(20)')],
}

# Text columns (type unchanged) whose values quote category/caste, disability or certificate kind
ENCRYPTED_TEXT: Dict[str, List[str]] = {
    'profile_facts': ['value'],
    'eligibility_evidence': ['requirement', 'profile_evidence', 'rule'],
    'exam_eligibility_evidence': ['requirement', 'profile_evidence', 'rule'],
    'skill_gaps': ['requirement', 'requirement_quote', 'profile_has', 'next_step'],
    'user_alerts': ['body'],
}
_BATCH = 500

def _text_table(table: str) -> sa.TableClause:
    return sa.table(table, sa.column('id', sa.Uuid()), *[sa.column(c, sa.Text()) for c in ENCRYPTED_TEXT[table]])

def _text_values_exist() -> bool:
    for table, cols in ENCRYPTED_TEXT.items():
        t = _text_table(table)
        if op.get_bind().execute(sa.select(t.c.id).where(sa.or_(*[t.c[c].is_not(None) for c in cols])).limit(1)).first():
            return True
    return False

def _rewrite_text(convert, wanted) -> None:
    """convert(value) every stored value for which wanted(value), batch by batch in id order."""
    for table, cols in ENCRYPTED_TEXT.items():
        t, after = _text_table(table), None
        while True:
            stmt = sa.select(t.c.id, *[t.c[c] for c in cols]).order_by(t.c.id).limit(_BATCH)
            rows = op.get_bind().execute(stmt if after is None else stmt.where(t.c.id > after)).mappings().all()
            if not rows:
                break
            for r in rows:
                values = {c: convert(r[c]) for c in cols if r[c] is not None and wanted(r[c])}
                if values:
                    op.get_bind().execute(t.update().where(t.c.id == r['id']).values(values))
            after = rows[-1]['id']

def _read(table: str, typed: bool) -> List[dict]:
    cols = [sa.column('id', sa.Uuid())] + [sa.column(c, t if typed else sa.Text()) for c, t, _ in ENCRYPTED[table]]
    rows = op.get_bind().execute(sa.select(*cols).select_from(sa.table(table))).mappings().all()
    names = [c for c, _, _ in ENCRYPTED[table]]
    return [dict(r) for r in rows if any(r[n] is not None for n in names)]

def _write(table: str, rows: List[dict], typed: bool) -> None:
    t = sa.table(table, sa.column('id', sa.Uuid()),
                 *[sa.column(c, ty if typed else sa.Text()) for c, ty, _ in ENCRYPTED[table]])
    for r in rows:
        op.get_bind().execute(t.update().where(t.c.id == r['id']).values({k: v for k, v in r.items() if k != 'id'}))

def upgrade() -> None:
    # Encrypt in memory first (and check the key if any quoting text exists), so a missing key fails before any change
    typed = {table: [{'id': r['id'], **{c: encrypt_value(r[c]) for c, _, _ in cols}} for r in _read(table, typed=True)]
             for table, cols in ENCRYPTED.items()}
    if _text_values_exist():
        require_key()

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('erasure_requested_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('erasure_due_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('erased_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('legal_hold_reason', sa.Text(), nullable=True))

    for table, cols in ENCRYPTED.items():
        with op.batch_alter_table(table, schema=None) as batch_op:
            for c, ty, _ in cols:
                batch_op.alter_column(c, existing_type=ty, type_=sa.Text(), postgresql_using=f'{c}::text')
        _write(table, typed[table], typed=False)
    _rewrite_text(encrypt_value, lambda v: not is_ciphertext(v))

def downgrade() -> None:
    _rewrite_text(decrypt_value, is_ciphertext)
    for table, cols in ENCRYPTED.items():
        rows = [{'id': r['id'], **{c: decrypt_value(r[c]) for c, _, _ in cols}} for r in _read(table, typed=False)]
        # Blank the ciphertext before the type change so the casts cannot fail (kind is NOT NULL: '' until rewritten)
        _write(table, [{'id': r['id'], **{c: ('' if c == 'kind' else None) for c, _, _ in cols}} for r in rows], typed=False)
        with op.batch_alter_table(table, schema=None) as batch_op:
            for c, ty, pg in cols:
                batch_op.alter_column(c, existing_type=sa.Text(), type_=ty, postgresql_using=f'{c}::{pg}')
        _write(table, rows, typed=True)

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('legal_hold_reason')
        batch_op.drop_column('erased_at')
        batch_op.drop_column('erasure_due_at')
        batch_op.drop_column('erasure_requested_at')
