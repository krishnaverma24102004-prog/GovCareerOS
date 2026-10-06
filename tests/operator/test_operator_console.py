"""
Operator console (cyber-cafe / CSC): operator isolation, consent before any client data, TEST-MODE billing charged
once per report, and the client's account claim. Vacancy/exam data is the real UPSC Advt 12/2026 PDF and the real
SSC CGL 2026 notice from tests/fixtures; users, names and prices are test-only values made up inside these tests.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import event, func, select

from govcareeros.backend.models import (
    Application, ClaimToken, ConsentRecord, LedgerEntry, OperatorReport, ProfileVersion, User, Vacancy,
)
from govcareeros.backend.services import billing
from govcareeros.backend.services import operator_console as oc
from govcareeros.backend.services.document_ingestion import get_or_create_source, ingest_pdf_bytes
from govcareeros.backend.services.exam_ingestion import ingest_exam_pdf
from govcareeros.backend.sources import ssc, upsc

FIXTURES = Path(__file__).parents[1] / "fixtures"
ADVT12 = FIXTURES / "upsc" / "AdvtNo-12-2026-Engl-250926_0.pdf"
LAW_OFFICER = "26091203226"  # Advt 12/2026
SSC_URLS = json.loads((FIXTURES / "ssc" / "sources.json").read_text(encoding="utf-8"))
CONSENT = {"client_present": True, "notice_version": oc.NOTICE_VERSION, "data_items": ["full_name", "personal_info", "education"]}

async def _law_officer(db_session) -> str:
    source = await get_or_create_source(db_session, upsc.UPSC_SOURCE)
    await ingest_pdf_bytes(db_session, source, f"https://www.upsc.gov.in/sites/default/files/{ADVT12.name}", ADVT12.read_bytes())
    await db_session.commit()
    return str((await db_session.execute(select(Vacancy.id).where(Vacancy.external_ref == LAW_OFFICER))).scalar_one())

async def _flag(db_session, email, **flags):
    user = (await db_session.execute(select(User).where(User.email == email))).scalar_one()
    for k, v in flags.items():
        setattr(user, k, v)
    await db_session.commit()

async def _operator(login, db_session, email):
    h = await login(email)
    await _flag(db_session, email, is_operator=True)
    return h

async def _client(api, h, name="Test Client One", consent=CONSENT):
    r = await api.post("/operator/clients", json={"full_name": name, "consent": consent}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()

async def _count(db_session, model) -> int:
    return (await db_session.execute(select(func.count()).select_from(model))).scalar_one()

@pytest.mark.asyncio
async def test_operator_role_is_granted_by_a_reviewer_only(api, login, db_session):
    admin, op, other = await login("admin@example.com"), await login("op@example.com"), await login("other@example.com")
    await _flag(db_session, "admin@example.com", is_reviewer=True)
    op_id = (await api.get("/auth/me", headers=op)).json()["id"]

    assert (await api.get("/operator/clients", headers=op)).status_code == 403
    assert (await api.post(f"/admin/users/{op_id}/operator", json={"is_operator": True}, headers=other)).status_code == 403
    r = await api.post(f"/admin/users/{op_id}/operator", json={"is_operator": True, "note": "CSC Ward 4"}, headers=admin)
    assert r.status_code == 200 and r.json()["is_operator"] is True
    me = (await api.get("/auth/me", headers=op)).json()
    assert me["is_operator"] is True
    assert (await api.get("/operator/clients", headers=op)).json() == []
    audit = (await api.get("/admin/audit?object_type=USER", headers=admin)).json()["items"]
    assert [(a["action"], a["note"]) for a in audit] == [("OPERATOR_GRANT", "CSC Ward 4")]

    # A managed client can never be made an operator
    client = await _client(api, op)
    assert (await api.post(f"/admin/users/{client['id']}/operator", json={"is_operator": True}, headers=admin)).status_code == 409

@pytest.mark.asyncio
async def test_no_client_data_is_stored_before_consent(api, login, db_session):
    op = await _operator(login, db_session, "op@example.com")
    users_before = await _count(db_session, User)

    # Client not present, stale notice, unknown items, or a name without consent to store it: nothing is created
    for consent in ({**CONSENT, "client_present": False}, {**CONSENT, "notice_version": "operator-assisted-2020-01"},
                    {**CONSENT, "data_items": ["aadhaar_number"]}, {**CONSENT, "data_items": ["education"]}):
        r = await api.post("/operator/clients", json={"full_name": "Test Client One", "consent": consent}, headers=op)
        assert r.status_code in (403, 422), r.text
    assert (await api.post("/operator/clients", json={"full_name": "Test Client One"}, headers=op)).status_code == 422
    assert await _count(db_session, User) == users_before and await _count(db_session, ConsentRecord) == 0

    # The INSERT of the client row carries no name; the consent row is written next; only then the name
    order = []
    def on_insert(mapper, conn, target):
        order.append(("insert", type(target).__name__, getattr(target, "full_name", None)))
    def on_update(mapper, conn, target):
        order.append(("update", type(target).__name__, getattr(target, "full_name", None)))
    for cls in (User, ConsentRecord):
        event.listen(cls, "after_insert", on_insert)
        event.listen(cls, "after_update", on_update)
    try:
        client = await _client(api, op, "Test Client One")
    finally:
        for cls in (User, ConsentRecord):
            event.remove(cls, "after_insert", on_insert)
            event.remove(cls, "after_update", on_update)
    assert order[:3] == [("insert", "User", None), ("insert", "ConsentRecord", None), ("update", "User", "Test Client One")]
    row = (await db_session.execute(select(User).where(User.id == uuid.UUID(client["id"])))).scalar_one()
    assert (row.email, row.hashed_password, row.claimed_at) == (None, "", None)
    record = (await db_session.execute(select(ConsentRecord).where(ConsentRecord.user_id == row.id))).scalar_one()
    me = (await api.get("/auth/me", headers=op)).json()
    assert (record.method, str(record.captured_by_id), record.purpose) == ("OPERATOR_ASSISTED", me["id"], oc.PURPOSE)
    assert json.loads(record.data_items) == ["education", "full_name", "personal_info"]
    assert client["consent"]["data_items"] == ["education", "full_name", "personal_info"] and client["claimed"] is False

    # Profile sections outside the consent are refused before anything is written
    versions = await _count(db_session, ProfileVersion)
    r = await api.put(f"/operator/clients/{client['id']}/profile", json={"experience": [
        {"organization": "Test Org", "designation": "Clerk"}]}, headers=op)
    assert r.status_code == 403 and "experience" in r.json()["detail"]
    assert await _count(db_session, ProfileVersion) == versions
    # Within the consent: the ordinary profile service writes a new version for the client
    r = await api.put(f"/operator/clients/{client['id']}/profile", json={"personal_info": {
        "full_name": "Test Client One", "category": "OBC", "gender": "Female"}}, headers=op)
    assert r.status_code == 201, r.text
    assert r.json()["version"]["version_number"] == 1 and r.json()["personal_info"]["category"] == "OBC"
    assert (await api.get(f"/operator/clients/{client['id']}/profile", headers=op)).json()["personal_info"]["gender"] == "Female"

    # Withdrawn consent: no further reads or writes, and the stored name is erased
    assert (await api.post(f"/operator/clients/{client['id']}/consent/withdraw", headers=op)).status_code == 204
    assert (await api.patch(f"/operator/clients/{client['id']}/profile", json={"personal_info": {"category": "SC"}},
                            headers=op)).status_code == 403
    assert (await api.get(f"/operator/clients/{client['id']}/profile", headers=op)).status_code == 403
    detail = (await api.get(f"/operator/clients/{client['id']}", headers=op)).json()
    assert detail["consent"] is None and detail["full_name"] is None and detail["profile_versions"] == []
    assert await _count(db_session, ProfileVersion) == versions + 1

    # A fresh consent with the client present restores access
    assert (await api.post(f"/operator/clients/{client['id']}/consent", json={**CONSENT, "client_present": False},
                           headers=op)).status_code == 422
    r = await api.post(f"/operator/clients/{client['id']}/consent", json=CONSENT, headers=op)
    assert r.status_code == 201 and r.json()["method"] == "OPERATOR_ASSISTED"
    assert (await api.get(f"/operator/clients/{client['id']}/profile", headers=op)).status_code == 200

@pytest.mark.asyncio
async def test_operator_sees_only_own_clients_and_reports(api, login, db_session):
    law = await _law_officer(db_session)
    op_a = await _operator(login, db_session, "op-a@example.com")
    op_b = await _operator(login, db_session, "op-b@example.com")
    plain = await login("plain@example.com")
    a1 = await _client(api, op_a, "Test Client A1")
    b1 = await _client(api, op_b, "Test Client B1")
    report = (await api.post(f"/operator/clients/{a1['id']}/reports", json={
        "kind": "vacancy", "target_id": law, "idempotency_key": "a1-law-0001"}, headers=op_a)).json()

    assert [c["id"] for c in (await api.get("/operator/clients", headers=op_a)).json()] == [a1["id"]]
    assert [c["id"] for c in (await api.get("/operator/clients", headers=op_b)).json()] == [b1["id"]]
    a1_path = f"/operator/clients/{a1['id']}"
    for method, path, body in [
        ("get", a1_path, None), ("get", f"{a1_path}/profile", None),
        ("put", f"{a1_path}/profile", {"personal_info": {"category": "SC"}}),
        ("post", f"{a1_path}/consent", CONSENT), ("post", f"{a1_path}/consent/withdraw", None),
        ("post", f"{a1_path}/claim-token", None),
        ("post", f"{a1_path}/reports", {"kind": "vacancy", "target_id": law, "idempotency_key": "b-steals-0001"}),
        ("get", f"/operator/reports/{report['id']}", None), ("get", f"/operator/reports/{report['id']}/checklist", None),
    ]:
        kw = {"json": body} if body is not None else {}
        r = await getattr(api, method)(path, headers=op_b, **kw)
        assert r.status_code == 404, (method, path, r.status_code)
        assert (await getattr(api, method)(path, headers=plain, **kw)).status_code == 403
    # Operator B's attempts left A's client untouched
    assert (await api.get(a1_path, headers=op_a)).json()["consent"] is not None
    assert await _count(db_session, OperatorReport) == 1
    assert (await api.get("/operator/statement", headers=op_b)).json()["entries"] == []

@pytest.mark.asyncio
async def test_report_creates_application_links_and_charges_once(api, login, db_session, monkeypatch):
    monkeypatch.setattr(billing.settings, "OPERATOR_REPORT_PRICE_PAISE", 2500)
    law = await _law_officer(db_session)
    op = await _operator(login, db_session, "op@example.com")
    client = await _client(api, op)
    v = (await api.put(f"/operator/clients/{client['id']}/profile", json={"personal_info": {
        "full_name": "Test Client One", "category": "OBC", "gender": "Male", "is_pwbd": False,
        "is_ex_serviceman": False, "is_central_govt_employee": False}}, headers=op)).json()["version"]["id"]

    body = {"kind": "vacancy", "target_id": law, "idempotency_key": "client1-law-0001"}
    first = await api.post(f"/operator/clients/{client['id']}/reports", json=body, headers=op)
    assert first.status_code == 201, first.text
    rep = first.json()
    assert rep["profile_version_id"] == v and rep["charge"]["amount_paise"] == 2500
    assert rep["charge"]["provider"] == "FAKE_TEST" and rep["charge"]["test_mode"] is True
    assert rep["charge"]["provider_ref"].startswith("fake_test_")
    snap = rep["snapshot"]
    assert snap["advertisement_number"] == "12/2026" and snap["kind"] == "vacancy" and snap["id"] == law
    assert snap["links"] == {"json": f"/apply-ready/vacancies/{law}", "html": f"/apply-ready/vacancies/{law}.html",
                             "personalised": f"/apply-ready/vacancies/{law}?profile_version_id={v}"}
    assert "official notification prevails" in snap["disclaimer"]
    app = (await db_session.execute(select(Application).where(Application.id == uuid.UUID(rep["application_id"])))).scalar_one()
    assert (str(app.user_id), str(app.vacancy_id), app.status) == (client["id"], law, "PLANNED")

    # Retry / double click with the same key: same report, same charge, nothing new
    again = await api.post(f"/operator/clients/{client['id']}/reports", json=body, headers=op)
    assert again.status_code == 200 and again.json()["id"] == rep["id"] and again.json()["charge"] == rep["charge"]
    # The same key for another target is a conflict, not a silent second report
    assert (await api.post(f"/operator/clients/{client['id']}/reports", json={**body, "target_id": str(uuid.uuid4())},
                           headers=op)).status_code == 409
    # Charging the stored report again (e.g. a retried background job) returns the one entry
    report = (await db_session.execute(select(OperatorReport).where(OperatorReport.id == uuid.UUID(rep["id"])))).scalar_one()
    provider = billing.FakeTestProvider()
    entry, created = await billing.charge_report(db_session, report, provider)
    assert created is False and str(entry.id) == rep["charge"]["id"] and provider.charges == {}
    assert await _count(db_session, LedgerEntry) == 1

    # A new report for the same vacancy (new key) reuses the client's application and is charged on its own
    monkeypatch.setattr(billing.settings, "OPERATOR_REPORT_PRICE_PAISE", 3000)
    second = (await api.post(f"/operator/clients/{client['id']}/reports", json={**body, "idempotency_key": "client1-law-0002"},
                             headers=op)).json()
    assert second["application_id"] == rep["application_id"] and second["charge"]["amount_paise"] == 3000
    assert await _count(db_session, Application) == 1 and await _count(db_session, LedgerEntry) == 2

    # The client's own cited checklist through the existing application_prep service
    r = await api.get(f"/operator/reports/{rep['id']}/checklist", headers=op)
    assert r.status_code == 200, r.text
    prep = r.json()
    assert prep["advertisement_number"] == "12/2026" and prep["profile_version_id"] == v and prep["fee"]["status"] == "PAY"

    # Monthly statement (IST month of the charges) lists both, test mode, nothing collected
    month = datetime.now(timezone.utc).astimezone(billing.IST).strftime("%Y-%m")
    st = (await api.get(f"/operator/statement?month={month}", headers=op)).json()
    assert st["test_mode"] is True and "TEST MODE" in st["note"]
    assert st["report_count"] == 2 and st["total_paise"] == 5500
    assert {e["client_name"] for e in st["entries"]} == {"Test Client One"}
    assert (await api.get("/operator/statement?month=2026-13", headers=op)).status_code == 422
    assert (await api.get("/operator/statement?month=2020-01", headers=op)).json()["entries"] == []

    # No report without consent; nothing charged
    assert (await api.post(f"/operator/clients/{client['id']}/consent/withdraw", headers=op)).status_code == 204
    r = await api.post(f"/operator/clients/{client['id']}/reports", json={**body, "idempotency_key": "client1-law-0003"}, headers=op)
    assert r.status_code == 403 and await _count(db_session, LedgerEntry) == 2
    assert (await api.get(f"/operator/reports/{rep['id']}/checklist", headers=op)).status_code == 403

@pytest.mark.asyncio
async def test_exam_report_on_the_real_ssc_cgl_notice(api, login, db_session):
    source = await get_or_create_source(db_session, ssc.SSC_SOURCE)
    cgl = "Notice_of_adv_cgl_2026.pdf"
    exam_id = str((await ingest_exam_pdf(db_session, source, SSC_URLS[cgl]["url"], (FIXTURES / "ssc" / cgl).read_bytes())).exam_id)
    await db_session.commit()
    op = await _operator(login, db_session, "op@example.com")
    client = await _client(api, op)
    r = await api.post(f"/operator/clients/{client['id']}/reports", json={
        "kind": "exam", "target_id": exam_id, "idempotency_key": "client1-cgl-0001"}, headers=op)
    assert r.status_code == 201, r.text
    rep = r.json()
    # No profile entered yet: links stay public and there is no personalised checklist
    assert rep["snapshot"]["links"] == {"json": f"/apply-ready/exams/{exam_id}", "html": f"/apply-ready/exams/{exam_id}.html",
                                        "personalised": None}
    assert rep["snapshot"]["document_url"] == SSC_URLS[cgl]["url"]
    assert (await api.get(f"/operator/reports/{rep['id']}/checklist", headers=op)).status_code == 409
    app = (await db_session.execute(select(Application).where(Application.id == uuid.UUID(rep["application_id"])))).scalar_one()
    assert str(app.exam_id) == exam_id and app.exam_ref == rep["snapshot"]["title"]

@pytest.mark.asyncio
async def test_prices_from_the_pricing_table(api, login, db_session, monkeypatch):
    monkeypatch.setattr(billing.settings, "OPERATOR_REPORT_PRICE_PAISE", 2000)
    admin = await login("admin@example.com")
    await _flag(db_session, "admin@example.com", is_reviewer=True)
    op = await _operator(login, db_session, "op@example.com")
    op2 = await _operator(login, db_session, "op2@example.com")
    op_id = (await api.get("/auth/me", headers=op)).json()["id"]
    op2_id = uuid.UUID((await api.get("/auth/me", headers=op2)).json()["id"])

    assert await billing.price_for(db_session, op2_id) == 2000
    assert (await api.post("/admin/billing/prices", json={"amount_paise": 1500}, headers=op)).status_code == 403
    assert (await api.post("/admin/billing/prices", json={"amount_paise": 1500}, headers=admin)).status_code == 201
    r = await api.post("/admin/billing/prices", json={"amount_paise": 1000, "operator_id": op_id}, headers=admin)
    assert r.status_code == 201, r.text
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    assert (await api.post("/admin/billing/prices", json={"amount_paise": 9900, "effective_from": future}, headers=admin)).status_code == 201
    assert await billing.price_for(db_session, uuid.UUID(op_id)) == 1000
    assert await billing.price_for(db_session, op2_id) == 1500
    assert await billing.price_for(db_session, op2_id, at=datetime.now(timezone.utc) + timedelta(days=31)) == 9900
    assert (await api.post("/admin/billing/prices", json={"amount_paise": -1}, headers=admin)).status_code == 422
    assert len((await api.get("/admin/billing/prices", headers=admin)).json()) == 3

def test_fake_provider_is_test_only_and_idempotent():
    p = billing.FakeTestProvider()
    assert p.test_mode is True and p.name == "FAKE_TEST"
    a = p.charge("report:1", 2000, "INR", "x")
    assert p.charge("report:1", 2000, "INR", "x") is a and len(p.charges) == 1
    assert p.charge("report:2", 2000, "INR", "x").reference != a.reference

@pytest.mark.asyncio
async def test_client_claims_account_with_one_time_token(api, login, db_session):
    op = await _operator(login, db_session, "op@example.com")
    client = await _client(api, op)
    first = (await api.post(f"/operator/clients/{client['id']}/claim-token", headers=op)).json()["token"]
    second = (await api.post(f"/operator/clients/{client['id']}/claim-token", headers=op)).json()["token"]
    # Only hashes are stored
    stored = {t.token_hash for t in (await db_session.execute(select(ClaimToken))).scalars()}
    assert first not in stored and second not in stored and len(stored) == 2

    claim = {"token": first, "email": "Client.One@example.com", "password": "client-own-password"}
    assert (await api.post("/auth/claim", json=claim)).status_code == 403  # replaced by the newer token
    assert (await api.post("/auth/claim", json={**claim, "token": second, "password": "short"})).status_code == 422
    await login("taken@example.com")
    assert (await api.post("/auth/claim", json={**claim, "token": second, "email": "taken@example.com"})).status_code == 409
    r = await api.post("/auth/claim", json={**claim, "token": second})
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "client.one@example.com" and r.json()["id"] == client["id"]
    assert (await api.post("/auth/claim", json={**claim, "token": second, "email": "again@example.com"})).status_code == 403

    r = await api.post("/auth/login", json={"email": "client.one@example.com", "password": "client-own-password"})
    assert r.status_code == 200
    me = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert (await api.get("/auth/me", headers=me)).json()["full_name"] == "Test Client One"
    # The client is not an operator and cannot use the console
    assert (await api.get("/operator/clients", headers=me)).status_code == 403
    assert (await api.get(f"/operator/clients/{client['id']}", headers=op)).json()["claimed"] is True
    assert (await api.post(f"/operator/clients/{client['id']}/claim-token", headers=op)).status_code == 409

@pytest.mark.asyncio
async def test_expired_claim_token_is_refused(db_session):
    operator = User(id=uuid.uuid4(), email="op@example.com", hashed_password="", is_operator=True)
    db_session.add(operator)
    await db_session.flush()
    client, _ = await oc.create_client(db_session, operator, "Test Client One", **CONSENT)
    past = datetime.now(timezone.utc) - timedelta(days=10)
    token, _ = await oc.issue_claim_token(db_session, operator, client, now=past)
    with pytest.raises(oc.OperatorError) as e:
        await oc.claim_account(db_session, token, "client@example.com", "client-own-password")
    assert e.value.status == 403 and client.claimed_at is None
