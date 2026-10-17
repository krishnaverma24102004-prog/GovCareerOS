"""
The DPDP migration encrypts category/disability values already stored, and the stored texts that quote them, decrypts
them on downgrade, and refuses to start when values exist but no key is configured. Throwaway SQLite file only; the
rows are test-only values written in the formats the app itself produces.
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

CERTIFICATE_CLAIM = ("OBC_NCL certificate; issued 2026-04-01; income financial year 2025-26; valid until not stated; "
                     "issued by not stated; Central Government format: not stated")
EVIDENCE = ("max age 33 for OBC", "DOB 1990-01-01, category OBC, age 36 on crucial date 2026-10-16; PwBD 45%",
            "age 36 within PwBD-relaxed limit 43")
GAP = ("max age 33 for OBC", "DOB 1990-01-01, category OBC, age 36 on crucial date 2026-10-16; PwBD 45%",
       "age fits only with a relaxation: needs a disability certificate in the prescribed format")
ALERT_BODY = "You are 33 on the crucial date; the OBC limit for this post is 33 (p.4)."
FACTS = 1201  # more than two of the migration's batches
NOW = "'2026-10-17 00:00:00', '2026-10-17 00:00:00'"  # created_at, updated_at (their server default is not SQLite SQL)

def _seed(db: Path) -> None:
    c = sqlite3.connect(db)
    u, v = uuid.uuid4().hex, uuid.uuid4().hex
    c.execute("insert into users(id, email, hashed_password, is_active, is_reviewer) values (?, 'm@test.invalid', 'x', 1, 0)", (u,))
    c.execute("insert into profile_versions(id, user_id, version_number) values (?, ?, 1)", (v, u))
    c.execute("insert into personal_info(id, profile_version_id, category, is_pwbd, disability_percentage, disability_types) "
              "values (?, ?, 'OBC', 1, 45, 'OL')", (uuid.uuid4().hex, v))
    c.execute("insert into reservation_certificates(id, profile_version_id, kind) values (?, ?, 'OBC_NCL')", (uuid.uuid4().hex, v))
    c.executemany("insert into profile_facts(id, profile_version_id, subject, predicate, value, status, created_at, updated_at) "
                  f"values (?, ?, 'reservation_certificates', 'certificate', ?, 'UNVERIFIED', {NOW})",
                  [(uuid.uuid4().hex, v, f"{CERTIFICATE_CLAIM} #{i}") for i in range(FACTS)])
    c.execute("insert into eligibility_evidence(id, result_id, check_name, requirement, profile_evidence, rule, outcome, created_at, updated_at) "
              f"values (?, ?, 'AGE', ?, ?, ?, 'CONDITIONAL', {NOW})", (uuid.uuid4().hex, uuid.uuid4().hex, *EVIDENCE))
    c.execute("insert into exam_eligibility_evidence(id, result_id, check_name, requirement, profile_evidence, rule, outcome, created_at, updated_at) "
              f"values (?, ?, 'AGE', ?, ?, ?, 'CONDITIONAL', {NOW})", (uuid.uuid4().hex, uuid.uuid4().hex, *EVIDENCE))
    c.execute("insert into skill_gaps(id, user_id, vacancy_id, gap_type, requirement, profile_has, next_step, blocking, created_at, updated_at) "
              f"values (?, ?, ?, 'CERTIFICATE', ?, ?, ?, 0, {NOW})", (uuid.uuid4().hex, u, uuid.uuid4().hex, *GAP))
    c.execute("insert into user_alerts(id, user_id, kind, dedupe_key, title, body, created_at, updated_at) values (?, ?, 'AGE_HEADROOM', "
              f"'age:x', 'Age limit close: Law Officer', ?, {NOW})", (uuid.uuid4().hex, u, ALERT_BODY))
    c.commit()
    c.close()

def _values(db: Path):
    c = sqlite3.connect(db)
    try:
        return (c.execute("select category, is_pwbd, disability_percentage, disability_types from personal_info").fetchone(),
                c.execute("select kind from reservation_certificates").fetchone()[0])
    finally:
        c.close()

def _texts(db: Path):
    c = sqlite3.connect(db)
    try:
        return {"facts": [r[0] for r in c.execute("select value from profile_facts")],
                "evidence": c.execute("select requirement, profile_evidence, rule from eligibility_evidence").fetchone(),
                "exam_evidence": c.execute("select requirement, profile_evidence, rule from exam_eligibility_evidence").fetchone(),
                "gap": c.execute("select requirement, profile_has, next_step, requirement_quote from skill_gaps").fetchone(),
                "alert": c.execute("select title, body from user_alerts").fetchone()}
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

    texts = _texts(db)
    assert len(texts["facts"]) == FACTS and all(crypto.is_ciphertext(v) for v in texts["facts"])
    assert sorted(crypto.decrypt_value(v) for v in texts["facts"]) == sorted(f"{CERTIFICATE_CLAIM} #{i}" for i in range(FACTS))
    for key, plain in (("evidence", EVIDENCE), ("exam_evidence", EVIDENCE), ("gap", GAP)):
        assert all(crypto.is_ciphertext(v) for v in texts[key][:3])
        assert tuple(crypto.decrypt_value(v) for v in texts[key][:3]) == plain
    assert texts["gap"][3] is None                                  # nothing stored stays nothing
    title, body = texts["alert"]
    assert title == "Age limit close: Law Officer" and crypto.decrypt_value(body) == ALERT_BODY

    command.downgrade(cfg, "-1")
    assert _values(db) == (("OBC", 1, 45, "OL"), "OBC_NCL")
    texts = _texts(db)
    assert sorted(texts["facts"]) == sorted(f"{CERTIFICATE_CLAIM} #{i}" for i in range(FACTS))
    assert texts["evidence"] == texts["exam_evidence"] == EVIDENCE and texts["gap"] == (*GAP, None)
    assert texts["alert"] == ("Age limit close: Law Officer", ALERT_BODY)

def test_refuses_to_run_without_a_key_when_values_exist(migration_db, monkeypatch):
    cfg, db = migration_db
    monkeypatch.setattr(settings, "FIELD_ENCRYPTION_KEY", SecretStr(""))
    with pytest.raises(crypto.FieldEncryptionError, match="not configured"):
        command.upgrade(cfg, REVISION)
    assert _values(db) == (("OBC", 1, 45, "OL"), "OBC_NCL")   # nothing changed
    assert _texts(db)["evidence"] == EVIDENCE

def test_refuses_to_run_without_a_key_when_only_quoting_texts_exist(tmp_path, monkeypatch):
    db = tmp_path / "dpdp_texts.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db.as_posix()}")
    cfg = _config()
    command.upgrade(cfg, ScriptDirectory.from_config(cfg).get_revision(REVISION).down_revision)
    c = sqlite3.connect(db)
    c.execute(f"insert into user_alerts(id, user_id, kind, dedupe_key, title, body, created_at, updated_at) values (?, ?, 'AGE_HEADROOM', 'age:x', 't', ?, {NOW})",
              (uuid.uuid4().hex, uuid.uuid4().hex, ALERT_BODY))
    c.commit()
    c.close()
    monkeypatch.setattr(settings, "FIELD_ENCRYPTION_KEY", SecretStr(""))
    with pytest.raises(crypto.FieldEncryptionError, match="not configured"):
        command.upgrade(cfg, REVISION)
    c = sqlite3.connect(db)
    try:
        assert c.execute("select body from user_alerts").fetchone()[0] == ALERT_BODY
        assert "erased_at" not in [r[1] for r in c.execute("pragma table_info(users)")]   # stopped before any change
    finally:
        c.close()
