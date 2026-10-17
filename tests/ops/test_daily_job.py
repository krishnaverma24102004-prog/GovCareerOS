"""
Daily job, run log and run lock. The radar step runs over the real UPSC advertisements captured on
29-09-2026 (Advt 11/2026 and 12/2026); "now" is pinned to that date.
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from govcareeros.backend.config.settings import settings
from govcareeros.backend.models import User
from govcareeros.backend.services import run_log
from govcareeros.backend.services.document_ingestion import get_or_create_source, ingest_pdf_bytes
from govcareeros.backend.sources import upsc
from govcareeros.scripts import daily_job

UPSC = Path(__file__).parents[1] / "fixtures" / "upsc"
NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)

def law_profile(dob):
    return {
        "personal_info": {"full_name": "Asha Rao", "date_of_birth": dob, "category": "OBC", "citizenship_status": "INDIAN_CITIZEN",
                          "is_pwbd": False, "is_ex_serviceman": False, "is_central_govt_employee": False},
        "education": [{"degree": "LL.B.", "institution": "Faculty of Law, University of Delhi", "end_date": "2016-06-30"}],
        "experience": [{"organization": "Rao & Associates", "designation": "Legal Associate", "domain": "Legal", "start_date": "2017-01-01"}],
    }

def test_run_log_is_append_only_and_read_newest_first(tmp_path):
    path = tmp_path / "logs" / "daily_job.jsonl"
    assert run_log.read_runs(path=path) == [] and run_log.last_run(path) is None
    for i in range(3):
        run_log.append_run({"run_id": str(i), "started_at": NOW}, path)
    with path.open("a", encoding="utf-8") as f:
        f.write('{"run_id": "cut off mid-wri')  # a crash while writing leaves at most one broken last line
    assert [r["run_id"] for r in run_log.read_runs(2, path)] == ["2", "1"]
    assert run_log.last_run(path)["started_at"] == NOW.isoformat()
    assert len(path.read_text(encoding="utf-8").splitlines()) == 4

def test_lock_blocks_a_second_run_until_released_or_stale(tmp_path):
    path = tmp_path / "daily_job.lock"
    with run_log.RunLock(path):
        assert json.loads(path.read_text())["pid"] == os.getpid()
        with pytest.raises(run_log.LockHeld):
            run_log.RunLock(path).acquire()
    assert not path.exists()

    # A run killed 7h ago left its lock behind: older than 6h, so the next run takes over
    run_log.RunLock(path).acquire()
    old = time.time() - 7 * 3600
    os.utime(path, (old, old))
    lock = run_log.RunLock(path).acquire()
    assert lock.took_over["pid"] == os.getpid()
    lock.release()

@pytest.mark.asyncio
async def test_daily_job_radar_step_over_real_upsc_data(api, login, db_session):
    source = await get_or_create_source(db_session, upsc.UPSC_SOURCE)
    for name in ("AdvtNo-11-2026-Engl-100926.pdf", "AdvtNo-12-2026-Engl-250926_0.pdf"):
        await ingest_pdf_bytes(db_session, source, f"https://www.upsc.gov.in/sites/default/files/{name}", (UPSC / name).read_bytes())
    await db_session.commit()
    await api.put("/profile", json=law_profile("1993-06-01"), headers=await login("asha@example.com"))
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, class_=AsyncSession)

    assert await daily_job.main(["--only", "radar", "--only", "digest"], session_factory=factory, now=NOW) == 0
    record = run_log.last_run()
    assert record["status"] == "OK" and record["failed_steps"] == []
    steps = {s["name"]: s for s in record["steps"]}
    assert [s["name"] for s in record["steps"]] == list(daily_job.STEPS)
    assert steps["radar"]["status"] == "ok" and steps["radar"]["counts"]["evaluations"] == 11
    assert steps["radar"]["counts"]["alerts_created"]["NEW_MATCH"] >= 1
    # The digest module is present: the step writes digest files (never emails)
    assert steps["digest"]["status"] == "ok"
    assert all(steps[s]["reason"] == "not selected" for s in ("discover", "upsc", "drafts"))
    assert not run_log.lock_path().exists()

    # A failing step is logged and the next one still runs (the radar finds nothing new this time)
    def broken_fetcher(**_):
        raise OSError("network down")
    assert await daily_job.main(["--skip", "upsc", "--skip", "drafts", "--skip", "digest"],
                                session_factory=factory, fetcher_factory=broken_fetcher, now=NOW) == 1
    record = run_log.last_run()
    steps = {s["name"]: s for s in record["steps"]}
    assert record["status"] == "FAILED" and record["failed_steps"] == ["discover"]
    assert steps["discover"]["error"] == "OSError: network down"
    assert steps["radar"]["status"] == "ok" and steps["radar"]["counts"]["evaluations"] == 0

    # While another run holds the lock nothing runs; the attempt is still logged
    with run_log.RunLock():
        assert await daily_job.main(["--only", "radar"], session_factory=factory, now=NOW) == 2
    assert run_log.last_run()["status"] == "LOCKED" and len(run_log.read_runs(10)) == 3

@pytest.mark.asyncio
async def test_daily_job_erase_step_hard_erases_accounts_past_their_grace_period(api, login, db_session):
    """POST /privacy/erase promises a hard erase after the grace period: the scheduled daily job carries it out."""
    h = await login("erase-due@test.invalid")
    assert (await api.put("/profile", json=law_profile("1993-06-01"), headers=h)).status_code == 201
    r = await api.post("/privacy/erase", json={"confirm": "ERASE"}, headers=h)
    assert r.status_code == 202, r.text
    due = datetime.fromisoformat(r.json()["erasure_due_at"])
    uid = (await db_session.execute(select(User.id).where(User.email == "erase-due@test.invalid"))).scalar_one()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, class_=AsyncSession)

    # Inside the grace period: nothing is erased
    assert await daily_job.main(["--only", "erase"], session_factory=factory, now=NOW) == 0
    steps = {s["name"]: s for s in run_log.last_run()["steps"]}
    assert steps["erase"] == {**steps["erase"], "status": "ok", "counts": {"erased": 0, "held": 0}}
    # Past it: the account is erased and the run log holds counts, not ids or emails
    assert await daily_job.main(["--only", "erase"], session_factory=factory, now=due) == 0
    record = run_log.last_run()
    assert {s["name"]: s for s in record["steps"]}["erase"]["counts"] == {"erased": 1, "held": 0}
    assert str(uid) not in json.dumps(record) and "erase-due@test.invalid" not in json.dumps(record)
    db_session.expire_all()
    user = await db_session.get(User, uid)
    assert user.erased_at is not None and user.email == f"erased-{uid}@erased.invalid"

@pytest.mark.asyncio
async def test_admin_runs_is_reviewer_only(api, login, db_session, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path / "storage"))
    run_log.append_run({"run_id": "a", "steps": [{"name": "radar", "status": "error", "traceback": "Traceback ..."}]})
    run_log.append_run({"run_id": "b", "steps": []})
    h = await login("ops@example.com")
    assert (await api.get("/admin/runs")).status_code == 401
    assert (await api.get("/admin/runs", headers=h)).status_code == 403
    user = (await db_session.execute(select(User).where(User.email == "ops@example.com"))).scalar_one()
    user.is_reviewer = True
    await db_session.commit()
    runs = (await api.get("/admin/runs?limit=5", headers=h)).json()
    assert [r["run_id"] for r in runs] == ["b", "a"] and "traceback" not in runs[1]["steps"][0]
    assert [r["run_id"] for r in (await api.get("/admin/runs?limit=1", headers=h)).json()] == ["b"]
