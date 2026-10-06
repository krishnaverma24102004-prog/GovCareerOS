"""
The DPDP migration encrypts category/disability values already stored, decrypts them on downgrade, and refuses to
start when values exist but no key is configured. Throwaway SQLite file only; the rows are test-only values.
"""
import sqlite3
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import SecretStr

from govcareeros.backend.config.settings import settings
from govcareeros.backend.privacy import crypto

ROOT = Path(__file__).parents[2]
REVISION = "e3b7a91c5d20"

def _config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    return cfg

def _seed(db: Path) -> None:
    c = sqlite3.connect(db)
    u, v = uuid.uuid4().hex, uuid.uuid4().hex
    c.execute("insert into users(id, email, hashed_password, is_active, is_reviewer) values (?, 'm@test.invalid', 'x', 1, 0)", (u,))
    c.execute("insert into profile_versions(id, user_id, version_number) values (?, ?, 1)", (v, u))
    c.execute("insert into personal_info(id, profile_version_id, category, is_pwbd, disability_percentage, disability_types) "
              "values (?, ?, 'OBC', 1, 45, 'OL')", (uuid.uuid4().hex, v))
    c.execute("insert into reservation_certificates(id, profile_version_id, kind) values (?, ?, 'OBC_NCL')", (uuid.uuid4().hex, v))
    c.commit()
    c.close()

def _values(db: Path):
    c = sqlite3.connect(db)
    try:
        return (c.execute("select category, is_pwbd, disability_percentage, disability_types from personal_info").fetchone(),
                c.execute("select kind from reservation_certificates").fetchone()[0])
    finally:
        c.close()

@pytest.fixture
def migration_db(tmp_path, monkeypatch):
    db = tmp_path / "dpdp.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db.as_posix()}")
    cfg = _config()
    command.upgrade(cfg, ScriptDirectory.from_config(cfg).get_revision(REVISION).down_revision)
    _seed(db)
    return cfg, db

def test_existing_values_are_encrypted_and_decrypted_back(migration_db):
    cfg, db = migration_db
    command.upgrade(cfg, REVISION)
    personal, kind = _values(db)
    assert all(crypto.is_ciphertext(v) for v in personal) and crypto.is_ciphertext(kind)
    assert [crypto.decrypt_value(v) for v in personal] == ["OBC", True, 45, "OL"] and crypto.decrypt_value(kind) == "OBC_NCL"

    command.downgrade(cfg, "-1")
    assert _values(db) == (("OBC", 1, 45, "OL"), "OBC_NCL")

def test_refuses_to_run_without_a_key_when_values_exist(migration_db, monkeypatch):
    cfg, db = migration_db
    monkeypatch.setattr(settings, "FIELD_ENCRYPTION_KEY", SecretStr(""))
    with pytest.raises(crypto.FieldEncryptionError, match="not configured"):
        command.upgrade(cfg, REVISION)
    assert _values(db) == (("OBC", 1, 45, "OL"), "OBC_NCL")   # nothing changed
