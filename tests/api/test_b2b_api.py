"""
B2B verified-notice API (/api/v1): API keys, scopes, rate limit, metering, cursor pagination, the change feed and
webhooks (signing, retries, SSRF refusal).

Notice data is real only:
  tests/fixtures/notices_snapshot.json        282 notice links discovered on official sites
  tests/fixtures/adapters/apsc/advt_2026.html  APSC's advertisement listing (closing dates), captured 2026-10-01
  tests/fixtures/ssc/                          SSC notice board JSON and notice PDFs (sources.json gives each URL)
Organisations, keys, webhook URLs and secrets are test-only values made inside each test; example.com /
example.net are reserved names (RFC 2606) and nothing is ever sent over the network (a fake wire answers).
"""
import hashlib
import hmac
import json
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpcore
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from govcareeros.backend.models import ApiKey, ApiUsage, RecruitmentNotice, ReviewAuditLog, User, Webhook, WebhookDelivery
from govcareeros.backend.schemas.attribution import DISCLAIMER
from govcareeros.backend.services import api_keys, b2b_events, b2b_notices, notice_verifier, webhooks
from govcareeros.backend.services.document_ingestion import get_or_create_source, ingest_pdf_bytes
from govcareeros.backend.services.exam_corrigenda import ingest_corrigendum_pdf
from govcareeros.backend.services.exam_ingestion import ingest_exam_pdf, run_ssc_pipeline
from govcareeros.backend.services.source_discovery import record_probe, sync_registry, url_hash
from govcareeros.backend.sources import ssc, upsc
from govcareeros.backend.sources.adapters import apsc
from govcareeros.backend.sources.generic_notices import PageFetcher, ProbeResult
from govcareeros.backend.sources.registry import SOURCES
from govcareeros.backend.sources.url_guard import GuardedTransport

FIXTURES = Path(__file__).parents[1] / "fixtures"
SSC = FIXTURES / "ssc"
SSC_URLS = json.loads((SSC / "sources.json").read_text(encoding="utf-8"))
SNAPSHOT = json.loads((FIXTURES / "notices_snapshot.json").read_text(encoding="utf-8"))["notices"]
APSC = FIXTURES / "adapters" / "apsc"
APSC_META = {p["file"]: p for p in json.loads((APSC / "meta.json").read_text(encoding="utf-8"))["pages"]}
CGL, REOPEN, SCHEDULE = "Notice_of_adv_cgl_2026.pdf", "CGLE_Reopen_23062026.pdf", "Important_Notice_2026_cgle_2026_12092026.pdf"
CHSL_URL = "https://ssc.gov.in/api/attachment/uploads/masterData/NoticeBoards/Notice_of_adv_chsle_2026.pdf"
# Verbatim from CGLE_Reopen_23062026.pdf p.1 (tests/ingestion/test_change_log.py)
REOPEN_QUOTE = ("re-open the window for submission of online applications for the Combined Graduate Level Examination, 2026 for "
                "two days i.e. from 23.06.2026 (23:00 hours) to 25.06.2026 (23:00 hours)")
HOOK_URL = "https://hooks.example.com/govcareeros"

@pytest.fixture(autouse=True)
def _fresh_limiter():
    api_keys.limiter.reset()
    yield
    api_keys.limiter.reset()

@pytest_asyncio.fixture
async def admin(api, login, db_session):
    h = await login("ops@example.com")
    (await db_session.execute(select(User).where(User.email == "ops@example.com"))).scalar_one().is_reviewer = True
    await db_session.commit()
    return h

@pytest.fixture
def new_key(api, admin):
    """Creates an org (once per name) and a key through the admin API; returns (key, key row JSON, org id)."""
    orgs = {}

    async def _new(scopes, org="Test Org Pvt Ltd", rate=None):
        if org not in orgs:
            r = await api.post("/admin/api/orgs", json={"name": org, "contact_email": "api@example.com"}, headers=admin)
            assert r.status_code == 201, r.text
            orgs[org] = r.json()["id"]
        r = await api.post(f"/admin/api/orgs/{orgs[org]}/keys", json={"name": "test key", "scopes": scopes,
                                                                      "rate_limit_per_minute": rate}, headers=admin)
        assert r.status_code == 201, r.text
        return r.json()["key"], r.json(), orgs[org]
    return _new

async def load_snapshot(db_session):
    sources = await sync_registry(db_session, [s for s in SOURCES if s.name in {n["source"] for n in SNAPSHOT}])
    for n in SNAPSHOT:
        seen = datetime.fromisoformat(n["first_seen_at"])
        db_session.add(RecruitmentNotice(
            source_id=sources[n["source"]].id, url=n["url"], url_hash=url_hash(n["url"]), title=n["title"],
            document_type=n["document_type"], notice_kind=n["notice_kind"], first_seen_at=seen, last_seen_at=seen,
            review_status=n["review_status"]))
    await db_session.commit()

async def load_apsc_advertisements(db_session):
    """APSC's captured advertisement listing through the APSC adapter and record_probe, as discovery stores it."""
    sources = await sync_registry(db_session, [s for s in SOURCES if s.name == "APSC"])
    page = APSC_META["advt_2026.html"]
    rows = apsc.parse_listing((APSC / "advt_2026.html").read_text(encoding="utf-8"), page["final_url"])
    await record_probe(db_session, sources["APSC"], ProbeResult("OK", 200, page["final_url"], listing_rows=rows),
                       datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc))
    await db_session.commit()
    return rows

def _replay_fetcher() -> PageFetcher:
    """Serves only the captured SSC responses; robots.txt answered 404 (text/html) at capture time."""
    captured = {SSC_URLS[name]["url"]: name for name in SSC_URLS if not name.startswith("_")}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://ssc.gov.in/robots.txt":
            return httpx.Response(404, headers={"content-type": "text/html"}, text="<html>404 Not Found</html>")
        name = captured.get(str(request.url))
        if name is None:
            return httpx.Response(404, headers={"content-type": "text/html"})
        return httpx.Response(200, headers={"content-type": "application/pdf" if name.endswith(".pdf") else "application/json; charset=utf-8"},
                              content=(SSC / name).read_bytes())
    return PageFetcher(allow_browser=False, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

async def cgl_exam(db_session, corrigenda=(REOPEN,)):
    source = await get_or_create_source(db_session, ssc.SSC_SOURCE)
    result = await ingest_exam_pdf(db_session, source, SSC_URLS[CGL]["url"], (SSC / CGL).read_bytes())
    for c in corrigenda:
        await ingest_corrigendum_pdf(db_session, source, SSC_URLS[c]["url"], (SSC / c).read_bytes(), title=SSC_URLS[c]["title"])
    await db_session.commit()
    return result.exam_id

# ---------- keys, scopes, metering ----------

@pytest.mark.asyncio
async def test_admin_issues_a_key_once_and_stores_only_its_hash(api, login, admin, db_session):
    outsider = await login("someone@example.com")
    assert (await api.post("/admin/api/orgs", json={"name": "X"}, headers=outsider)).status_code == 403
    assert (await api.post("/admin/api/orgs", json={"name": "X"})).status_code == 401

    org = (await api.post("/admin/api/orgs", json={"name": "Acme Careers"}, headers=admin)).json()
    assert (await api.post("/admin/api/orgs", json={"name": "Acme Careers"}, headers=admin)).status_code == 409
    bad = await api.post(f"/admin/api/orgs/{org['id']}/keys", json={"name": "k", "scopes": ["notices:write"]}, headers=admin)
    assert bad.status_code == 422
    assert (await api.post(f"/admin/api/orgs/{org['id']}/keys", json={"name": "k", "scopes": []}, headers=admin)).status_code == 422

    r = await api.post(f"/admin/api/orgs/{org['id']}/keys", json={"name": "prod", "scopes": ["notices:read", "changes:read"]}, headers=admin)
    assert r.status_code == 201, r.text
    created = r.json()
    key = created["key"]
    assert key.startswith("gco_") and key.startswith(created["prefix"]) and len(created["prefix"]) == 12
    assert created["scopes"] == ["changes:read", "notices:read"] and created["rate_limit_per_minute"] == 60

    row = (await db_session.execute(select(ApiKey))).scalar_one()
    assert row.key_hash == hashlib.sha256(key.encode()).hexdigest()
    assert key not in json.dumps({c.name: str(getattr(row, c.name)) for c in ApiKey.__table__.columns})
    listed = (await api.get(f"/admin/api/orgs/{org['id']}/keys", headers=admin)).json()
    assert [k["prefix"] for k in listed] == [created["prefix"]] and "key" not in listed[0]
    actions = {a.action for a in (await db_session.execute(select(ReviewAuditLog))).scalars()}
    assert {"API_ORG_CREATE", "API_KEY_CREATE"} <= actions

@pytest.mark.asyncio
async def test_key_scopes_revocation_and_usage_metering(api, admin, new_key, db_session):
    await load_snapshot(db_session)
    key, row, org_id = await new_key(["notices:read"])
    h = {"X-API-Key": key}

    assert (await api.get("/api/v1/notices")).status_code == 401
    assert (await api.get("/api/v1/notices", headers={"X-API-Key": "gco_notakey_x"})).status_code == 401
    assert (await api.get("/api/v1/notices", headers={"Authorization": f"Bearer {key}"})).status_code == 401  # header is X-API-Key
    assert (await api.get("/api/v1/notices", headers=h)).status_code == 200
    assert (await api.get("/api/v1/notices", params={"limit": 5}, headers=h)).status_code == 200
    assert (await api.get("/api/v1/changes", headers=h)).status_code == 403
    assert (await api.post("/api/v1/verify", data={"text": "x"}, headers=h)).status_code == 403
    assert (await api.get("/api/v1/webhooks", headers=h)).status_code == 403
    # An unknown notice is refused after metering: a failed request is rolled back and not billed
    assert (await api.get(f"/api/v1/notices/{row['id']}", headers=h)).status_code == 404

    usage = (await api.get(f"/admin/api/orgs/{org_id}/usage", headers=admin)).json()
    assert [(u["endpoint"], u["requests"], u["prefix"]) for u in usage] == [("notices.list", 2, row["prefix"])]
    assert usage[0]["day"] == datetime.now(timezone.utc).date().isoformat()
    assert (await db_session.get(ApiKey, uuid.UUID(row["id"]))).last_used_at is not None

    r = await api.post(f"/admin/api/keys/{row['id']}/revoke", headers=admin)
    assert r.status_code == 200 and r.json()["revoked_at"]
    assert (await api.get("/api/v1/notices", headers=h)).status_code == 401
    assert {a.action for a in (await db_session.execute(select(ReviewAuditLog))).scalars()} >= {"API_KEY_REVOKE"}

@pytest.mark.asyncio
async def test_rate_limit_per_key_is_a_token_bucket(api, new_key, db_session, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(api_keys, "limiter", api_keys.RateLimiter(clock=lambda: clock[0]))
    slow, _, _ = await new_key(["notices:read"], rate=2)
    other, _, _ = await new_key(["notices:read"], rate=2)

    assert [(await api.get("/api/v1/notices", headers={"X-API-Key": slow})).status_code for _ in range(2)] == [200, 200]
    r = await api.get("/api/v1/notices", headers={"X-API-Key": slow})
    assert r.status_code == 429 and r.headers["Retry-After"] == "30" and r.headers["X-RateLimit-Limit"] == "2"
    # Buckets are per key
    assert (await api.get("/api/v1/notices", headers={"X-API-Key": other})).status_code == 200
    clock[0] += 30  # 2 per minute: one token back after 30 s
    assert (await api.get("/api/v1/notices", headers={"X-API-Key": slow})).status_code == 200
    assert (await api.get("/api/v1/notices", headers={"X-API-Key": slow})).status_code == 429
    usage = (await db_session.execute(select(ApiUsage))).scalars().all()
    assert sorted(u.requests for u in usage) == [1, 3]  # 429s are not billed

def test_bucket_refills_continuously_and_caps_at_capacity():
    t = [0.0]
    lim = api_keys.RateLimiter(clock=lambda: t[0])
    k = uuid.uuid4()
    assert all(lim.take(k, 60) is None for _ in range(60))
    assert lim.take(k, 60) == pytest.approx(1.0)
    t[0] += 3600  # an idle hour refills to capacity, not beyond
    assert sum(lim.take(k, 60) is None for _ in range(100)) == 60

# ---------- notices ----------

@pytest.mark.asyncio
async def test_notice_list_cursor_walks_every_notice_once(api, new_key, db_session):
    await load_snapshot(db_session)
    key, _, _ = await new_key(["notices:read"])
    h = {"X-API-Key": key}
    seen, cursor, pages = [], None, 0
    while True:
        r = await api.get("/api/v1/notices", params={"limit": 50, **({"cursor": cursor} if cursor else {})}, headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        seen += body["items"]
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert pages == 6 and len(seen) == len(SNAPSHOT) == 282
    assert len({n["id"] for n in seen}) == 282 and {n["url"] for n in seen} == {n["url"] for n in SNAPSHOT}
    keys = [(n["first_seen_at"], n["id"]) for n in seen]
    assert keys == sorted(keys)
    first = seen[0]
    assert first["disclaimer"] == DISCLAIMER and first["attribution"]["source_name"] == first["source"]
    assert first["attribution"]["document_url"] == first["url"]  # not downloaded yet: the official link itself

    # Filters, each checked against the snapshot itself
    crpf = (await api.get("/api/v1/notices", params={"source": "CRPF", "limit": 200}, headers=h)).json()["items"]
    assert len(crpf) == sum(n["source"] == "CRPF" for n in SNAPSHOT) == 63
    corr = (await api.get("/api/v1/notices", params={"kind": "CORRIGENDUM", "limit": 200}, headers=h)).json()["items"]
    assert len(corr) == sum(n["notice_kind"] == "CORRIGENDUM" for n in SNAPSHOT) and all(n["kind"] == "CORRIGENDUM" for n in corr)
    union = (await api.get("/api/v1/notices", params={"jurisdiction": "IN", "limit": 200}, headers=h)).json()
    assert len(union["items"]) == 200 and union["next_cursor"]
    assert (await api.get("/api/v1/notices", params={"jurisdiction": "IN-HR"}, headers=h)).json()["items"] == []
    assert (await api.get("/api/v1/notices", params={"jurisdiction": "Haryana"}, headers=h)).status_code == 422
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert (await api.get("/api/v1/notices", params={"updated_since": future}, headers=h)).json()["items"] == []
    past = (await api.get("/api/v1/notices", params={"updated_since": "2026-01-01T00:00:00", "limit": 10}, headers=h)).json()
    assert len(past["items"]) == 10
    # A forged cursor is refused, not misread
    assert (await api.get("/api/v1/notices", params={"cursor": "bm90LWEtY3Vyc29y"}, headers=h)).status_code == 422
    assert (await api.get("/api/v1/notices", params={"cursor": "%%%"}, headers=h)).status_code == 422

@pytest.mark.asyncio
async def test_status_filter_reads_the_listing_closing_date(api, new_key, db_session, monkeypatch):
    rows = await load_apsc_advertisements(db_session)
    day = date(2026, 8, 1)
    monkeypatch.setattr(b2b_notices, "_today", lambda: day)
    key, _, _ = await new_key(["notices:read"])
    h = {"X-API-Key": key}

    by_status = {s: (await api.get("/api/v1/notices", params={"status": s, "source": "APSC"}, headers=h)).json()["items"]
                 for s in ("open", "closed")}
    expect_open = {r.url for r in rows if r.last_date and r.last_date >= day}
    expect_closed = {r.url for r in rows if r.last_date and r.last_date < day}
    assert expect_open and expect_closed  # the captured listing has both
    assert {n["url"] for n in by_status["open"]} == expect_open and {n["url"] for n in by_status["closed"]} == expect_closed
    assert {n["status"] for n in by_status["open"]} == {"open"} and {n["status"] for n in by_status["closed"]} == {"closed"}

    everything = (await api.get("/api/v1/notices", params={"source": "APSC"}, headers=h)).json()["items"]
    ra = next(n for n in everything if n["url"] == "https://apsc.nic.in/advt_2026/Advt_no_12-2026_website.pdf")
    # tests/adapters/test_apsc.py: Research Assistant, Advt 12/2026, published 04-08-2026, last date 10-09-2026
    assert (ra["advertisement_number"], ra["published_on"], ra["last_date"]) == ("12/2026", "2026-08-04", "2026-09-10")
    assert ra["listing_evidence"]["last_date"] == "10-09-2026" and ra["attribution"]["official_page_url"] == APSC_META["advt_2026.html"]["final_url"]
    assert ra["jurisdiction"] == "IN-AS" and ra["attribution"]["jurisdiction"] == "IN-AS"
    unknown = [n for n in everything if n["last_date"] is None]
    assert all(n["status"] == "unknown" for n in unknown)
    assert len(everything) == len(expect_open) + len(expect_closed) + len(unknown)

@pytest.mark.asyncio
async def test_notice_detail_carries_parsed_requirements_with_page_and_quote(api, new_key, db_session):
    async with _replay_fetcher() as fetcher:
        run = await run_ssc_pipeline(db_session, fetcher, pages=1, first_page=3)
    assert run["errors"] == []
    await db_session.commit()
    notice = (await db_session.execute(select(RecruitmentNotice).where(RecruitmentNotice.url == CHSL_URL))).scalar_one()
    key, _, _ = await new_key(["notices:read"])

    r = await api.get(f"/api/v1/notices/{notice.id}", headers={"X-API-Key": key})
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["source"], d["kind"], d["url"], d["disclaimer"]) == ("SSC", "EXAM_NOTICE", CHSL_URL, DISCLAIMER)
    assert d["attribution"]["document_url"] == CHSL_URL and d["attribution"]["document_retrieved_at"]
    assert d["vacancies"] == []
    (exam,) = d["exams"]
    assert exam["name"] == "Combined Higher Secondary (10+2) Level Examination, 2026" and exam["fee_amount"] == 100
    assert exam["attribution"]["document_url"] == CHSL_URL and exam["attribution"]["page"] is not None
    assert exam["requirements"]
    cited = [q for q in exam["requirements"] if q["page"] and q["quote"]]
    assert cited, exam["requirements"]
    assert all(q["page"] >= 1 for q in cited)
    assert (await api.get(f"/api/v1/notices/{uuid.uuid4()}", headers={"X-API-Key": key})).status_code == 404

# ---------- change feed ----------

@pytest.mark.asyncio
async def test_change_feed_is_cursor_ordered_and_misses_nothing(api, new_key, db_session):
    exam_id = await cgl_exam(db_session)
    assert await b2b_events.collect(db_session) == {"notice.new": 0, "notice.changed": 1}
    await db_session.commit()
    assert await b2b_events.collect(db_session) == {"notice.new": 0, "notice.changed": 0}  # recorded once
    key, _, _ = await new_key(["changes:read"])
    h = {"X-API-Key": key}

    page = (await api.get("/api/v1/changes", headers=h)).json()
    (event,) = page["items"]
    assert event["type"] == "notice.changed" and event["exam_id"] == str(exam_id) and page["next_cursor"] == event["cursor"]
    data = event["data"]
    assert data["subject"]["name"].startswith("Combined Graduate Level Examination, 2026") and data["disclaimer"] == DISCLAIMER
    last = next(c for c in data["changes"] if c["field"] == "LAST_DATE")
    assert (last["old"], last["new"]) == ("2026-06-22T23:00:00+05:30", "2026-06-25T23:00:00+05:30")
    assert (last["old_evidence"]["url"], last["old_evidence"]["page"]) == (SSC_URLS[CGL]["url"], 1)
    assert last["old_evidence"]["quote"] == "Last date and time for receipt of online applications 22.06.2026 (23:00 hours)"
    assert (last["new_evidence"]["url"], last["new_evidence"]["page"], last["new_evidence"]["quote"]) == (SSC_URLS[REOPEN]["url"], 1, REOPEN_QUOTE)

    # Polling with the cursor: nothing new, same cursor back
    again = (await api.get("/api/v1/changes", params={"since": page["next_cursor"]}, headers=h)).json()
    assert again["items"] == [] and again["next_cursor"] == page["next_cursor"]

    # A later official change and newly discovered notices arrive after the cursor, in order
    source = await get_or_create_source(db_session, ssc.SSC_SOURCE)
    await ingest_corrigendum_pdf(db_session, source, SSC_URLS[SCHEDULE]["url"], (SSC / SCHEDULE).read_bytes(), title=SSC_URLS[SCHEDULE]["title"])
    await db_session.commit()
    await load_snapshot(db_session)
    assert await b2b_events.collect(db_session) == {"notice.new": 282, "notice.changed": 1}
    await db_session.commit()
    newer = (await api.get("/api/v1/changes", params={"since": page["next_cursor"], "type": "notice.changed"}, headers=h)).json()
    (schedule,) = newer["items"]
    (tier1,) = schedule["data"]["changes"]
    assert (tier1["field"], tier1["new"]) == ("EXAM_DATE", "30th September, 2026 to 30th October, 2026")
    assert tier1["new_evidence"]["page"] == 1 and tier1["new_evidence"]["url"] == SSC_URLS[SCHEDULE]["url"]

    walked, cursor = [], page["next_cursor"]
    while True:
        p = (await api.get("/api/v1/changes", params={"since": cursor, "limit": 100}, headers=h)).json()
        if not p["items"]:
            break
        walked += p["items"]
        cursor = p["next_cursor"]
    assert len(walked) == 283 and [e["type"] for e in walked].count("notice.new") == 282
    seqs = [b2b_events.feed_seq(e["cursor"]) for e in walked]
    assert seqs == sorted(seqs) and len(set(seqs)) == 283
    new_one = next(e for e in walked if e["type"] == "notice.new")
    assert new_one["data"]["notice"]["id"] == new_one["notice_id"] and new_one["data"]["notice"]["attribution"]
    assert (await api.get("/api/v1/changes", params={"since": "garbage!"}, headers=h)).status_code == 422

# ---------- verify ----------

@pytest.mark.asyncio
async def test_v1_verify_is_the_public_check_behind_a_key(api, new_key, db_session, monkeypatch):
    source = await get_or_create_source(db_session, upsc.UPSC_SOURCE)
    name = "AdvtNo-12-2026-Engl-250926_0.pdf"
    url = f"https://www.upsc.gov.in/sites/default/files/{name}"
    await ingest_pdf_bytes(db_session, source, url, (FIXTURES / "upsc" / name).read_bytes())
    await db_session.commit()
    key, _, org_id = await new_key(["verify:use"])
    h = {"X-API-Key": key}

    assert (await api.post("/api/v1/verify", data={"url": url})).status_code == 401
    r = await api.post("/api/v1/verify", files={"file": (name, (FIXTURES / "upsc" / name).read_bytes(), "application/pdf")}, headers=h)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["verdict"] == notice_verifier.FOUND
    law = next(m for m in out["matches"] if m["kind"] == "VACANCY" and m["title"] == "Law Officer")
    assert law["page"] == 6 and "Three vacancies for the post of Law Officer" in law["quote"]
    assert (await api.post("/api/v1/verify", data={}, headers=h)).status_code == 422
    # Same body limit as the public check, before parsing
    monkeypatch.setattr(notice_verifier, "MAX_REQUEST_BYTES", 1000)
    big = await api.post("/api/v1/verify", files={"file": (name, b"%PDF-" + b"0" * 5000, "application/pdf")}, headers=h)
    assert big.status_code == 413

# ---------- webhooks ----------

class _Wire(httpx.AsyncHTTPTransport):
    """Stands in for the network under GuardedTransport: records requests, answers from a script (or from
    `route(request)`, which may return a response or raise)."""
    def __init__(self, *args, answers=(), route=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.answers, self.route, self.requests = list(answers), route, []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        self.requests.append(request)
        if self.route:
            return self.route(request)
        return self.answers.pop(0) if self.answers else httpx.Response(200)

class _Net(GuardedTransport, _Wire):
    pass

def _client(addresses, answers=(), route=None):
    async def resolve(host, port):
        return list(addresses)
    transport = _Net(resolve=resolve, answers=answers, route=route)
    return httpx.AsyncClient(transport=transport, follow_redirects=False), transport

@pytest.mark.parametrize("url", [
    "http://hooks.example.com/x", "https://127.0.0.1/x", "https://10.1.2.3/x", "https://192.168.0.10/x", "https://172.16.5.4/",
    "https://[::1]/x", "https://[fd00::1]/x", "https://169.254.169.254/latest/meta-data", "https://localhost/x",
    "https://api.localhost/x", "https://intranet/x", "https://printer.local/x", "https://svc.internal/x",
    "https://user:pw@hooks.example.com/x", "https://127.1/x", "https://2130706433/x", "https://0x7f000001/x",
    "https://0.0.0.0/x", "ftp://hooks.example.com/x", "https:///x",
    # Hosts httpx cannot encode (IDNA) or that are no host name at all: refused at registration, not at delivery
    "https://☃.example.com/x", "https://xn--zz.example.com/x", "https://ＥＸＡＭＰＬＥ.com/x",
    "https://１２７.０.０.１/x", "https://exa mple.com/x", "https://ex%41mple.com/x",
    "https://a..b.example.com/x", "https://-a.example.com/x",
])
def test_webhook_url_guard_refuses_non_public_targets(url):
    assert webhooks.url_refusal(url) is not None

def test_webhook_url_guard_accepts_public_https():
    assert webhooks.url_refusal(HOOK_URL) is None
    assert webhooks.url_refusal("https://8.8.8.8/hook") is None  # a public address literal
    assert webhooks.url_refusal("https://bücher.example/hook") is None  # a valid IDN (sent as xn--bcher-kva)

def test_signature_is_hmac_sha256_of_timestamp_and_body():
    body, secret, ts = b'{"type":"notice.new"}', "whsec_test-only", 1_790_000_000
    header = webhooks.signature_header(secret, ts, body)
    assert header == f"t={ts},v1=" + hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    assert webhooks.verify_signature(secret, header, body, now=ts + 10)
    assert not webhooks.verify_signature(secret, header, body + b" ", now=ts)
    assert not webhooks.verify_signature("whsec_other", header, body, now=ts)
    assert not webhooks.verify_signature(secret, header, body, now=ts + 301)  # replayed later
    assert not webhooks.verify_signature(secret, "garbage", body, now=ts)

@pytest.mark.asyncio
async def test_webhook_registration_refuses_private_urls_through_the_api(api, new_key, db_session):
    key, _, _ = await new_key(["webhooks:manage"])
    h = {"X-API-Key": key}
    for url in ("https://127.0.0.1/hook", "https://10.0.0.8/hook", "http://hooks.example.com/hook", "https://[::1]/"):
        r = await api.post("/api/v1/webhooks", json={"url": url}, headers=h)
        assert r.status_code == 422, (url, r.text)
    assert (await api.post("/api/v1/webhooks", json={"url": HOOK_URL, "events": ["notice.deleted"]}, headers=h)).status_code == 422
    r = await api.post("/api/v1/webhooks", json={"url": HOOK_URL}, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["secret"].startswith("whsec_") and r.json()["events"] == ["notice.changed", "notice.new"]
    listed = (await api.get("/api/v1/webhooks", headers=h)).json()
    assert [w["url"] for w in listed] == [HOOK_URL] and "secret" not in listed[0]
    # Another org cannot see or remove it
    other, _, _ = await new_key(["webhooks:manage"], org="Other Org")
    assert (await api.get("/api/v1/webhooks", headers={"X-API-Key": other})).json() == []
    assert (await api.delete(f"/api/v1/webhooks/{listed[0]['id']}", headers={"X-API-Key": other})).status_code == 404

@pytest.mark.asyncio
async def test_webhook_delivery_is_signed_retried_with_backoff_and_logged(api, new_key, db_session):
    await cgl_exam(db_session, corrigenda=())  # the notice alone: no official change yet
    await b2b_events.collect(db_session)
    await db_session.commit()
    key, _, _ = await new_key(["webhooks:manage"])
    h = {"X-API-Key": key}
    created = (await api.post("/api/v1/webhooks", json={"url": HOOK_URL, "events": ["notice.changed"]}, headers=h)).json()
    secret = created["secret"]

    # Recorded after registration: the re-open corrigendum
    source = await get_or_create_source(db_session, ssc.SSC_SOURCE)
    await ingest_corrigendum_pdf(db_session, source, SSC_URLS[REOPEN]["url"], (SSC / REOPEN).read_bytes(), title=SSC_URLS[REOPEN]["title"])
    await db_session.commit()
    await b2b_events.collect(db_session)
    t0 = datetime(2026, 10, 6, 6, 0, tzinfo=timezone.utc)
    assert await webhooks.enqueue(db_session, now=t0) == 1
    assert await webhooks.enqueue(db_session, now=t0) == 0
    await db_session.commit()

    client, wire = _client(["93.184.215.14"], answers=[httpx.Response(500), httpx.Response(302, headers={"location": "https://10.0.0.1/"})])
    async with client:
        assert await webhooks.deliver_due(db_session, client, now=t0) == {"DELIVERED": 0, "RETRY": 1, "FAILED": 0, "REFUSED": 0}
        (d,) = (await db_session.execute(select(WebhookDelivery))).scalars().all()
        assert (d.status, d.attempts, d.response_status, d.next_attempt_at) == ("PENDING", 1, 500, t0 + timedelta(minutes=1))
        # Not due yet: nothing sent
        assert await webhooks.deliver_due(db_session, client, now=t0 + timedelta(seconds=59)) == {"DELIVERED": 0, "RETRY": 0, "FAILED": 0, "REFUSED": 0}
        t1 = t0 + timedelta(minutes=1)
        await webhooks.deliver_due(db_session, client, now=t1)  # a redirect is not followed
        assert (d.status, d.attempts, d.next_attempt_at) == ("PENDING", 2, t1 + timedelta(minutes=5)) and "redirects" in d.error
        t2 = t1 + timedelta(minutes=5)
        assert (await webhooks.deliver_due(db_session, client, now=t2))["DELIVERED"] == 1
    await db_session.commit()
    assert (d.status, d.attempts, d.delivered_at, d.response_status) == ("DELIVERED", 3, t2, 200)
    assert [str(r.url) for r in wire.requests] == [HOOK_URL] * 3  # never the redirect target

    last = wire.requests[-1]
    body = last.content
    assert last.headers["X-GovCareerOS-Event"] == "notice.changed" and last.headers["X-GovCareerOS-Delivery"] == str(d.id)
    assert webhooks.verify_signature(secret, last.headers["X-GovCareerOS-Signature"], body, now=t2.timestamp())
    assert not webhooks.verify_signature(secret, last.headers["X-GovCareerOS-Signature"], body.replace(b"25.06", b"26.06"), now=t2.timestamp())
    payload = json.loads(body)
    assert payload["type"] == "notice.changed" and payload["data"]["disclaimer"] == DISCLAIMER
    assert any(c["new_evidence"]["quote"] == REOPEN_QUOTE for c in payload["data"]["changes"])

    log = (await api.get(f"/api/v1/webhooks/{created['id']}/deliveries", headers=h)).json()
    assert [(x["status"], x["attempts"], x["event_type"]) for x in log] == [("DELIVERED", 3, "notice.changed")]

@pytest.mark.asyncio
async def test_delivery_gives_up_after_the_last_backoff(api, new_key, db_session):
    key, _, _ = await new_key(["webhooks:manage"])
    await api.post("/api/v1/webhooks", json={"url": HOOK_URL}, headers={"X-API-Key": key})
    await cgl_exam(db_session)
    await b2b_events.collect(db_session)
    await webhooks.enqueue(db_session)
    await db_session.commit()
    client, wire = _client(["93.184.215.14"], answers=[httpx.Response(503)] * 10)
    now = datetime(2026, 10, 6, tzinfo=timezone.utc)
    async with client:
        for _ in range(webhooks.MAX_ATTEMPTS):
            (d,) = (await db_session.execute(select(WebhookDelivery))).scalars().all()
            now = d.next_attempt_at
            await webhooks.deliver_due(db_session, client, now=now)
        assert (d.status, d.attempts, d.next_attempt_at) == ("FAILED", webhooks.MAX_ATTEMPTS, None)
        assert await webhooks.deliver_due(db_session, client, now=now + timedelta(days=1)) == {"DELIVERED": 0, "RETRY": 0, "FAILED": 0, "REFUSED": 0}
    assert len(wire.requests) == webhooks.MAX_ATTEMPTS

@pytest.mark.asyncio
async def test_never_delivers_to_a_private_address(api, new_key, db_session):
    key, _, _ = await new_key(["webhooks:manage"])
    h = {"X-API-Key": key}
    hook = (await api.post("/api/v1/webhooks", json={"url": HOOK_URL}, headers=h)).json()
    await cgl_exam(db_session)
    await b2b_events.collect(db_session)
    await webhooks.enqueue(db_session)
    await db_session.commit()

    # The public-looking name resolves inside the network (DNS rebinding / internal record): refused, not retried
    client, wire = _client(["10.0.0.7"])
    async with client:
        assert (await webhooks.deliver_due(db_session, client, now=datetime.now(timezone.utc)))["REFUSED"] == 1
    (d,) = (await db_session.execute(select(WebhookDelivery))).scalars().all()
    assert (d.status, d.next_attempt_at) == ("REFUSED", None) and "non-public" in d.error
    assert wire.requests == []

    # A stored URL that is no longer allowed is refused before any lookup
    row = await db_session.get(Webhook, uuid.UUID(hook["id"]))
    row.url = "https://169.254.169.254/latest/meta-data"
    d.status, d.next_attempt_at = "PENDING", datetime(2026, 1, 1, tzinfo=timezone.utc)
    await db_session.commit()
    client, wire = _client(["93.184.215.14"])
    async with client:
        assert (await webhooks.deliver_due(db_session, client))["REFUSED"] == 1
    assert wire.requests == []

    # The production client resolves through the guard and never follows redirects
    real = webhooks.make_client()
    assert isinstance(real._transport, GuardedTransport) and real.follow_redirects is False
    await real.aclose()

@pytest.mark.asyncio
async def test_delivery_connects_only_to_the_address_that_was_checked(api, new_key, db_session):
    """DNS rebinding: the name answers a public address to the guard's lookup and loopback to any later one. The
    production client connects to the checked address (TLS and Host still name the host), never to the name."""
    key, _, _ = await new_key(["webhooks:manage"])
    await api.post("/api/v1/webhooks", json={"url": HOOK_URL}, headers={"X-API-Key": key})
    await cgl_exam(db_session)
    await b2b_events.collect(db_session)
    await webhooks.enqueue(db_session)
    await db_session.commit()

    lookups = []
    async def resolve(host, port):  # hand-built rebinding DNS
        lookups.append(host)
        return ["93.184.215.14"] if len(lookups) == 1 else ["127.0.0.1"]
    connects, tls_names = [], []

    class Net(httpcore.AsyncNetworkBackend):  # the network below the guard: records, answers 204, sends nothing
        async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            connects.append((host, port))

            class Stream(httpcore.AsyncMockStream):
                async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
                    tls_names.append(server_hostname)
                    return self
            return Stream([b"HTTP/1.1 204 No Content\r\n\r\n"])

        async def sleep(self, seconds):
            pass

    async with webhooks.make_client(resolve=resolve, network_backend=Net()) as client:
        assert (await webhooks.deliver_due(db_session, client))["DELIVERED"] == 1
    assert lookups == ["hooks.example.com"] and connects == [("93.184.215.14", 443)] and tls_names == ["hooks.example.com"]

@pytest.mark.asyncio
async def test_one_bad_webhook_does_not_stop_delivery_to_the_others(api, new_key, db_session):
    key, _, _ = await new_key(["webhooks:manage"])
    h = {"X-API-Key": key}
    urls = (HOOK_URL, "https://broken.example.net/hook", "https://idna.example.net/hook")
    ids = [(await api.post("/api/v1/webhooks", json={"url": u}, headers=h)).json()["id"] for u in urls]
    # A row stored before registration refused hosts httpx cannot encode
    (await db_session.get(Webhook, uuid.UUID(ids[2]))).url = "https://xn--zz.example.com/hook"
    await cgl_exam(db_session)
    await b2b_events.collect(db_session)
    assert await webhooks.enqueue(db_session) == 3
    await db_session.commit()

    def route(request):
        if request.url.host == "broken.example.net":
            raise RuntimeError("test-only failure inside the transport")
        return httpx.Response(200)
    client, wire = _client(["93.184.215.14"], route=route)
    now = datetime.now(timezone.utc)
    async with client:
        assert await webhooks.deliver_due(db_session, client, now=now) == {"DELIVERED": 1, "RETRY": 1, "FAILED": 0, "REFUSED": 1}
        # Nothing is sent again: every outcome was recorded
        assert await webhooks.deliver_due(db_session, client, now=now) == {"DELIVERED": 0, "RETRY": 0, "FAILED": 0, "REFUSED": 0}
    await db_session.commit()
    by_hook = {str(d.webhook_id): d for d in (await db_session.execute(select(WebhookDelivery))).scalars()}
    assert by_hook[ids[0]].status == "DELIVERED"
    broken = by_hook[ids[1]]
    assert (broken.status, broken.attempts, broken.next_attempt_at) == ("PENDING", 1, now + timedelta(minutes=1))
    assert broken.error == "unexpected error (RuntimeError)"
    assert by_hook[ids[2]].status == "REFUSED" and by_hook[ids[2]].next_attempt_at is None
    assert sorted(r.url.host for r in wire.requests) == ["broken.example.net", "hooks.example.com"]  # each once

@pytest.mark.asyncio
async def test_removed_webhook_and_earlier_events_are_not_delivered(api, new_key, db_session):
    await cgl_exam(db_session)
    await b2b_events.collect(db_session)  # recorded before the webhook exists
    await db_session.commit()
    key, _, _ = await new_key(["webhooks:manage"])
    h = {"X-API-Key": key}
    hook = (await api.post("/api/v1/webhooks", json={"url": HOOK_URL}, headers=h)).json()
    assert await webhooks.enqueue(db_session) == 0

    await load_snapshot(db_session)
    await b2b_events.collect(db_session)
    assert await webhooks.enqueue(db_session) == 282
    await db_session.commit()
    assert (await api.delete(f"/api/v1/webhooks/{hook['id']}", headers=h)).status_code == 204
    statuses = {d.status for d in (await db_session.execute(select(WebhookDelivery))).scalars()}
    assert statuses == {"CANCELLED"}
    client, wire = _client(["93.184.215.14"])
    async with client:
        assert (await webhooks.deliver_due(db_session, client))["DELIVERED"] == 0
    assert wire.requests == []
    assert (await api.get("/api/v1/webhooks", headers=h)).json()[0]["disabled_at"]

# ---------- docs ----------

@pytest.mark.asyncio
async def test_openapi_groups_the_v1_endpoints(api):
    doc = (await api.get("/api/v1/openapi.json")).json()
    assert doc["paths"] and all(p.startswith("/api/v1/") for p in doc["paths"])
    assert {"/api/v1/notices", "/api/v1/notices/{notice_id}", "/api/v1/changes", "/api/v1/verify", "/api/v1/webhooks"} <= set(doc["paths"])
    assert doc["components"]["securitySchemes"]["ApiKey"] == {
        "type": "apiKey", "in": "header", "name": "X-API-Key", "description": "An organisation's API key (gco_...), issued by GovCareerOS"}
    tags = {t for p in doc["paths"].values() for op in p.values() for t in op["tags"]}
    assert tags == {"v1: notices", "v1: changes", "v1: verify", "v1: webhooks"}
    assert all(op.get("security") == [{"ApiKey": []}] for p in doc["paths"].values() for op in p.values())
    full = (await api.get("/openapi.json")).json()
    assert "/admin/api/orgs" in full["paths"] and "/api/v1/notices" in full["paths"]

def test_readme_examples_carry_no_real_key():
    readme = (Path(__file__).parents[2] / "docs" / "api" / "README.md").read_text(encoding="utf-8")
    assert "X-API-Key: $GCO_API_KEY" in readme
    assert not re.search(r"gco_[A-Za-z0-9]{8}_[A-Za-z0-9_-]{20,}", readme)
