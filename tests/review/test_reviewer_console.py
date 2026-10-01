"""
Reviewer console on real data: the 282 notice links GovCareerOS discovered on official sites
(tests/fixtures/notices_snapshot.json, captured 2026-10-01) and the real BSF walk-in notice with the
recorded nemotron answer (tests/fixtures/llm, replayed, not generated).
"""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from govcareeros.backend.documents.pdf_text import extract_pages, tessdata_dir
from govcareeros.backend.llm.provider import LLMResponse
from govcareeros.backend.models import ExtractionDraft, Fact, RecruitmentNotice, ReviewAuditLog, User
from govcareeros.backend.services.document_ingestion import store_raw_document
from govcareeros.backend.services.extraction_drafts import create_draft
from govcareeros.backend.services.review_console import ATTENTION_STATUSES, URGENT_KINDS, _sort_key
from govcareeros.backend.services.source_discovery import sync_registry, url_hash
from govcareeros.backend.sources.registry import SOURCES

FIXTURES = Path(__file__).parents[1] / "fixtures"
SNAPSHOT = json.loads((FIXTURES / "notices_snapshot.json").read_text(encoding="utf-8"))["notices"]
ATTENTION = [n for n in SNAPSHOT if n["review_status"] in ATTENTION_STATUSES or n["review_status"].startswith("FETCH_FAILED_")]

async def _load_snapshot(db_session):
    names = {n["source"] for n in SNAPSHOT}
    sources = await sync_registry(db_session, [s for s in SOURCES if s.name in names])
    by_url = {}
    for n in SNAPSHOT:
        seen = datetime.fromisoformat(n["first_seen_at"])
        notice = RecruitmentNotice(id=uuid.uuid4(), source_id=sources[n["source"]].id, url=n["url"], url_hash=url_hash(n["url"]),
                                   title=n["title"], document_type=n["document_type"], notice_kind=n["notice_kind"],
                                   first_seen_at=seen, last_seen_at=seen, review_status=n["review_status"])
        db_session.add(notice)
        by_url[(n["source"], n["url"])] = notice
    await db_session.commit()
    return sources, by_url

async def _make_reviewer(db_session, email):
    (await db_session.execute(select(User).where(User.email == email))).scalar_one().is_reviewer = True
    await db_session.commit()

async def _all_items(api, h, **params):
    items, offset = [], 0
    while True:
        r = await api.get("/review/queue", params={**params, "limit": 100, "offset": offset}, headers=h)
        assert r.status_code == 200, r.text
        page = r.json()
        items += page["items"]
        offset += 100
        if offset >= page["total"]:
            assert len(items) == page["total"]
            return items

@pytest.mark.asyncio
async def test_queue_orders_and_filters_real_notices(api, login, db_session):
    await _load_snapshot(db_session)
    h = await login("reviewer@example.com")
    await _make_reviewer(db_session, "reviewer@example.com")

    items = await _all_items(api, h)
    assert len(ATTENTION) == 275 and len(items) == 275
    assert {i["url"] for i in items} == {n["url"] for n in ATTENTION}
    assert all(i["type"] == "NOTICE" for i in items)
    # No priority column yet: vacancies and corrigenda first, newest first within each tier
    urgent = [i["notice_kind"] in URGENT_KINDS for i in items]
    assert urgent == sorted(urgent, reverse=True) and urgent.count(True) == 146 + 8
    for tier in (True, False):
        seen = [i["first_seen_at"] for i, u in zip(items, urgent) if u == tier]
        assert seen == sorted(seen, reverse=True)
    # Already-handled notices (PARSED, DRAFTED, REJECTED) are not in the queue
    assert not {i["status"] for i in items} & {"PARSED", "DRAFTED", "REJECTED"}

    crpf = await _all_items(api, h, source="CRPF")
    assert len(crpf) == sum(n["source"] == "CRPF" for n in ATTENTION) and {i["source"] for i in crpf} == {"CRPF"}
    corr = await _all_items(api, h, kind="CORRIGENDUM")
    assert sorted(i["title"] for i in corr) == sorted(n["title"] for n in ATTENTION if n["notice_kind"] == "CORRIGENDUM")
    new = await _all_items(api, h, status="NEW")
    assert {i["title"] for i in new} == {n["title"] for n in SNAPSHOT if n["review_status"] == "NEW"} and len(new) == 4
    assert [i["source"] for i in await _all_items(api, h, status="DRAFT_FAILED")] == ["MHA"]
    rules = await _all_items(api, h, q="recruitment RULES")
    assert len(rules) == sum("recruitment rules" in n["title"].lower() for n in ATTENTION) > 0
    assert await _all_items(api, h, type="DRAFT") == []
    # Pagination is bounded
    assert (await api.get("/review/queue", params={"limit": 201}, headers=h)).status_code == 422
    assert (await api.get("/review/queue", params={"limit": 0}, headers=h)).status_code == 422
    page2 = (await api.get("/review/queue", params={"limit": 10, "offset": 10}, headers=h)).json()
    assert [i["id"] for i in page2["items"]] == [i["id"] for i in items[10:20]] and page2["total"] == 275

def test_triage_priority_outranks_the_fallback_when_present():
    """When the triage module's priority is set it decides first; unset items keep the kind/recency order."""
    at = lambda s: datetime.fromisoformat(s)
    result, vacancy = (next(n for n in ATTENTION if n["notice_kind"] == k) for k in ("RESULT", "VACANCY"))
    item = lambda n, p: dict(type="NOTICE", priority=p, notice_kind=n["notice_kind"], first_seen_at=at(n["first_seen_at"]), title=n["title"])
    assert sorted([item(result, None), item(vacancy, None)], key=_sort_key)[0]["notice_kind"] == "VACANCY"
    assert sorted([item(vacancy, None), item(result, 5)], key=_sort_key)[0]["notice_kind"] == "RESULT"
    assert sorted([item(vacancy, 1), item(result, 5)], key=_sort_key)[0]["notice_kind"] == "RESULT"
    assert sorted([item(result, "LOW"), item(vacancy, "HIGH")], key=_sort_key)[0]["notice_kind"] == "VACANCY"

@pytest.mark.asyncio
async def test_console_is_reviewer_only_and_has_no_bulk_approve(api, login, db_session):
    h = await login("asha@example.com")
    any_id = uuid.uuid4()
    calls = [
        ("GET", "/review/queue", None), ("GET", "/review/queue/facets", None), ("GET", f"/review/drafts/{any_id}/pages", None),
        ("POST", "/review/notices/bulk", {"notice_ids": [str(any_id)], "action": "SKIP"}),
        ("POST", "/review/drafts/bulk-reject", {"draft_ids": [str(any_id)], "note": "not a vacancy"}),
        ("GET", "/review/facts", None), ("POST", f"/review/facts/{any_id}/verify", {}),
        ("POST", f"/review/facts/{any_id}/reject", {"note": "x"}),
        ("GET", "/admin/users", None), ("POST", f"/admin/users/{any_id}/reviewer", {"is_reviewer": True}),
        ("GET", "/admin/audit", None),
    ]
    for method, path, body in calls:
        assert (await api.request(method, path, json=body, headers=h)).status_code == 403, path
        assert (await api.request(method, path, json=body)).status_code == 401, path
    # A non-reviewer cannot make themselves a reviewer
    me = (await api.get("/auth/me", headers=h)).json()
    assert (await api.post(f"/admin/users/{me['id']}/reviewer", json={"is_reviewer": True}, headers=h)).status_code == 403
    assert (await db_session.execute(select(ReviewAuditLog))).scalars().all() == []

    # Approval exists only per draft: no route approves several at once
    paths = (await api.get("/openapi.json")).json()["paths"]
    assert sorted(p for p in paths if "approve" in p) == ["/review/drafts/{draft_id}/approve"]
    assert sorted(p for p in paths if "bulk" in p) == ["/review/drafts/bulk-reject", "/review/notices/bulk"]
    await _make_reviewer(db_session, "asha@example.com")
    for path in ("/review/drafts/bulk-approve", "/review/notices/bulk-approve", "/review/bulk-approve"):
        assert (await api.post(path, json={"draft_ids": [str(any_id)]}, headers=h)).status_code in (404, 405)
    r = await api.post("/review/notices/bulk", json={"notice_ids": [str(any_id)], "action": "APPROVE"}, headers=h)
    assert r.status_code == 422

@pytest.mark.asyncio
async def test_bulk_notice_actions_are_recorded_and_all_or_nothing(api, login, db_session):
    sources, by_url = await _load_snapshot(db_session)
    h = await login("reviewer@example.com")
    await _make_reviewer(db_session, "reviewer@example.com")
    pick = lambda src, status: [by_url[(n["source"], n["url"])] for n in SNAPSHOT if n["source"] == src and n["review_status"] == status]
    crpf = pick("CRPF", "MANUAL_REVIEW")[:3]
    ids = [str(n.id) for n in crpf]

    r = await api.post("/review/notices/bulk", json={"notice_ids": ids, "action": "SKIP", "note": "  results page, not a vacancy "}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 3 and set(r.json()["status"].values()) == {"SKIPPED"}
    reviewer = (await db_session.execute(select(User).where(User.email == "reviewer@example.com"))).scalar_one()
    for n in crpf:
        await db_session.refresh(n)
        assert (n.review_status, n.reviewer_id, n.review_note) == ("SKIPPED", reviewer.id, "results page, not a vacancy")
        assert n.reviewed_at is not None
    queue_ids = {i["id"] for i in await _all_items(api, h)}
    assert not queue_ids & set(ids) and len(queue_ids) == 272
    assert {i["id"] for i in await _all_items(api, h, status="SKIPPED")} == set(ids)

    audit = (await api.get("/admin/audit", params={"object_type": "NOTICE"}, headers=h)).json()
    assert audit["total"] == 3
    assert {(a["action"], a["actor_email"], a["note"], a["detail"]["from"], a["detail"]["to"]) for a in audit["items"]} == {
        ("NOTICE_SKIP", "reviewer@example.com", "results page, not a vacancy", "MANUAL_REVIEW", "SKIPPED")}
    assert {a["object_id"] for a in audit["items"]} == set(ids)

    # Requeue returns a notice to the status discovery gives it: MANUAL_REVIEW without a parser, NEW with one
    upsc_new = pick("UPSC", "NEW")[0]
    assert sources["UPSC"].adapter_name
    r = await api.post("/review/notices/bulk", json={"notice_ids": [str(upsc_new.id)], "action": "NOT_REQUIRED"}, headers=h)
    assert r.json()["status"] == {str(upsc_new.id): "NOT_REQUIRED"}
    r = await api.post("/review/notices/bulk", json={"notice_ids": ids[:1] + [str(upsc_new.id)], "action": "REQUEUE"}, headers=h)
    assert r.json()["status"] == {ids[0]: "MANUAL_REVIEW", str(upsc_new.id): "NEW"}

    # A parsed or drafted notice is decided elsewhere: the whole request is refused and nothing changes
    parsed_id = pick("UPSC", "PARSED")[0].id  # plain value: a refused request rolls the shared session back
    r = await api.post("/review/notices/bulk", json={"notice_ids": ids[1:] + [str(parsed_id)], "action": "NOT_REQUIRED"}, headers=h)
    assert r.status_code == 409 and "PARSED" in r.json()["detail"]
    r = await api.post("/review/notices/bulk", json={"notice_ids": ids[1:] + [str(uuid.uuid4())], "action": "NOT_REQUIRED"}, headers=h)
    assert r.status_code == 404
    statuses = (await db_session.execute(select(RecruitmentNotice.review_status).where(
        RecruitmentNotice.id.in_([uuid.UUID(ids[1]), uuid.UUID(ids[2]), parsed_id])).execution_options(populate_existing=True))).scalars().all()
    assert sorted(statuses) == ["PARSED", "SKIPPED", "SKIPPED"]
    actions = [a["action"] for a in (await api.get("/admin/audit", headers=h)).json()["items"]]
    assert sorted(actions) == sorted(["NOTICE_SKIP"] * 3 + ["NOTICE_NOT_REQUIRED", "NOTICE_REQUEUE", "NOTICE_REQUEUE"])

@pytest.mark.asyncio
async def test_fact_verification_needs_a_document_and_another_reviewer(api, login, db_session):
    candidate = await login("candidate@example.com")
    reviewer = await login("reviewer@example.com")
    await _make_reviewer(db_session, "reviewer@example.com")
    profile = {"education": [{"degree": "MBBS", "institution": "AIIMS", "end_date": "1995-06-30"}]}
    assert (await api.put("/profile", json=profile, headers=candidate)).status_code == 201
    degree = (await api.post("/profile/facts", json={"subject": "education", "predicate": "degree", "value": "MBBS"}, headers=candidate)).json()
    other = (await api.post("/profile/facts", json={"subject": "education", "predicate": "institution", "value": "AIIMS"}, headers=candidate)).json()

    facts = (await api.get("/review/facts", headers=reviewer)).json()
    assert facts["total"] == 2 and {f["id"] for f in facts["items"]} == {degree["id"], other["id"]}
    assert all(not f["own"] and f["evidence"] == [] for f in facts["items"])

    # Nothing stored to check the claim against: cannot be verified
    r = await api.post(f"/review/facts/{degree['id']}/verify", json={}, headers=reviewer)
    assert r.status_code == 409 and "document" in r.json()["detail"]
    # The candidate attaches a stored document (a real official PDF from the fixtures stands in as the artifact)
    from govcareeros.backend.services.document_ingestion import get_or_create_source
    from govcareeros.backend.sources import upsc
    source = await get_or_create_source(db_session, upsc.UPSC_SOURCE)
    url = "https://www.upsc.gov.in/sites/default/files/AdvtNo-12-2026-Engl-250926_0.pdf"
    raw, _ = await store_raw_document(db_session, source, url, (FIXTURES / "upsc" / "AdvtNo-12-2026-Engl-250926_0.pdf").read_bytes(),
                                      200, "application/pdf")
    await db_session.commit()
    r = await api.post(f"/profile/facts/{degree['id']}/evidence", json={"evidence_type": "DOCUMENT", "raw_document_id": str(raw.id),
                                                                        "source": "degree certificate"}, headers=candidate)
    assert r.status_code == 201, r.text

    chain = next(f for f in (await api.get("/review/facts", headers=reviewer)).json()["items"] if f["id"] == degree["id"])
    assert [(e["evidence_type"], e["artifact_url"], e["artifact_content_hash"]) for e in chain["evidence"]] == [
        ("DOCUMENT", url, raw.content_hash)]
    # The candidate is not a reviewer
    assert (await api.post(f"/review/facts/{degree['id']}/verify", json={}, headers=candidate)).status_code == 403

    r = await api.post(f"/review/facts/{degree['id']}/verify", json={"note": "matches certificate"}, headers=reviewer)
    assert r.status_code == 200, r.text
    me = (await api.get("/auth/me", headers=reviewer)).json()
    body = r.json()
    assert body["status"] == "VERIFIED" and body["reviewer_id"] == me["id"] and body["review_note"] == "matches certificate"
    assert [e["evidence_type"] for e in body["evidence"]] == ["DOCUMENT", "APPROVED"]
    assert (await api.post(f"/review/facts/{degree['id']}/verify", json={}, headers=reviewer)).status_code == 409
    # The candidate sees the decision on their own fact
    assert (await api.get(f"/profile/facts/{degree['id']}", headers=candidate)).json()["status"] == "VERIFIED"

    assert (await api.post(f"/review/facts/{other['id']}/reject", json={"note": " "}, headers=reviewer)).status_code == 422
    r = await api.post(f"/review/facts/{other['id']}/reject", json={"note": "no document for this claim"}, headers=reviewer)
    assert r.status_code == 200 and r.json()["status"] == "REJECTED"
    assert (await api.get("/review/facts", params={"status": "UNVERIFIED"}, headers=reviewer)).json()["total"] == 0
    assert (await api.get("/review/facts", params={"status": "VERIFIED"}, headers=reviewer)).json()["total"] == 1

    # A reviewer's own fact: listed as own, and neither verifiable nor rejectable by them
    assert (await api.put("/profile", json=profile, headers=reviewer)).status_code == 201
    mine = (await api.post("/profile/facts", json={"subject": "education", "predicate": "degree", "value": "MBBS"}, headers=reviewer)).json()
    listed = (await api.get("/review/facts", headers=reviewer)).json()["items"]
    assert [(f["id"], f["own"]) for f in listed] == [(mine["id"], True)]
    assert (await api.post(f"/review/facts/{mine['id']}/verify", json={}, headers=reviewer)).status_code == 403
    assert (await api.post(f"/review/facts/{mine['id']}/reject", json={"note": "x"}, headers=reviewer)).status_code == 403
    assert (await db_session.execute(select(Fact.status).where(Fact.id == uuid.UUID(mine["id"])))).scalar_one() == "UNVERIFIED"

    audit = (await api.get("/admin/audit", params={"object_type": "FACT"}, headers=reviewer)).json()["items"]
    assert sorted((a["action"], a["object_id"]) for a in audit) == sorted([("FACT_VERIFY", degree["id"]), ("FACT_REJECT", other["id"])])

@pytest.mark.asyncio
async def test_admin_users_and_last_reviewer(api, login, db_session):
    a = await login("first@example.com")
    b = await login("second@example.com")
    await _make_reviewer(db_session, "first@example.com")
    users = (await api.get("/admin/users", headers=a)).json()
    assert [(u["email"], u["is_reviewer"]) for u in users] == [("first@example.com", True), ("second@example.com", False)]
    assert set(users[0]) == {"id", "email", "full_name", "is_active", "is_reviewer"}
    first_id, second_id = users[0]["id"], users[1]["id"]

    r = await api.post(f"/admin/users/{first_id}/reviewer", json={"is_reviewer": False}, headers=a)
    assert r.status_code == 409 and "last reviewer" in r.json()["detail"]
    r = await api.post(f"/admin/users/{second_id}/reviewer", json={"is_reviewer": True, "note": "second pair of eyes"}, headers=a)
    assert r.status_code == 200 and r.json()["is_reviewer"] is True
    assert (await api.get("/review/queue", headers=b)).status_code == 200
    assert (await api.post(f"/admin/users/{first_id}/reviewer", json={"is_reviewer": False}, headers=a)).status_code == 200
    assert (await api.get("/review/queue", headers=a)).status_code == 403
    assert (await api.post(f"/admin/users/{second_id}/reviewer", json={"is_reviewer": False}, headers=b)).status_code == 409
    assert (await api.post(f"/admin/users/{uuid.uuid4()}/reviewer", json={"is_reviewer": True}, headers=b)).status_code == 404

    audit = (await api.get("/admin/audit", params={"object_type": "USER"}, headers=b)).json()["items"]
    assert [(x["action"], x["actor_email"], x["object_id"]) for x in audit] == [
        ("REVIEWER_REVOKE", "first@example.com", first_id), ("REVIEWER_GRANT", "first@example.com", second_id)]
    assert audit[1]["note"] == "second pair of eyes"

# --- the real BSF draft -----------------------------------------------------------------------------------------

LLM = FIXTURES / "llm"
PDF = LLM / "BSF_e0ee6e833f.pdf"
RECORDING = json.loads((LLM / "BSF_e0ee6e833f.nemotron-3-super-120b-a12b.llm.json").read_text(encoding="utf-8"))
BSF_URL = "https://rectt.bsf.gov.in/static/bsf/pdf/specialist-doctors-walk-in.pdf"
needs_ocr = pytest.mark.skipif(tessdata_dir() is None, reason="the BSF notice is scanned; needs Tesseract")

class ReplayProvider:
    """Returns the model's recorded answer (same replay as tests/extraction/test_llm_drafts.py)."""
    name = "nvidia"
    chunk_chars = 120_000

    def __init__(self, recording):
        self.model = recording["model"]
        self.prompt_version = recording["prompt_version"]
        self._answers = [c["raw"] for c in recording["chunks"]]

    async def complete_json(self, system, user, schema):
        return LLMResponse(self._answers.pop(0), self.model, 0.0)

async def _bsf_draft(db_session):
    sources = await sync_registry(db_session, [s for s in SOURCES if s.name == "BSF"])
    now = datetime.now(timezone.utc)
    notice = RecruitmentNotice(source_id=sources["BSF"].id, url=BSF_URL, url_hash=url_hash(BSF_URL), title="Walk-in: Specialist Doctors and GDMOs",
                               document_type="PDF", notice_kind="VACANCY", first_seen_at=now, last_seen_at=now, review_status="MANUAL_REVIEW")
    db_session.add(notice)
    await db_session.flush()
    draft = await create_draft(db_session, sources["BSF"], BSF_URL, PDF.read_bytes(), ReplayProvider(RECORDING), notice)
    await db_session.commit()
    return draft.id, notice.id

def _ws(s):
    return " ".join(s.split())

@needs_ocr
@pytest.mark.asyncio
async def test_pages_endpoint_highlights_the_real_bsf_quotes(api, login, db_session):
    await _load_snapshot(db_session)
    draft_id, notice_id = await _bsf_draft(db_session)
    h = await login("reviewer@example.com")
    await _make_reviewer(db_session, "reviewer@example.com")

    # The draft is in the queue (its notice is DRAFTED, so it appears once, as the draft), and first: newest vacancy
    items = await _all_items(api, h)
    assert items[0]["type"] == "DRAFT" and items[0]["id"] == str(draft_id) and items[0]["notice_id"] == str(notice_id)
    assert items[0]["source"] == "BSF" and items[0]["vacancies"] == 2 and len(items) == 276
    assert [i["id"] for i in await _all_items(api, h, type="DRAFT")] == [str(draft_id)]
    assert [i["id"] for i in await _all_items(api, h, status="PENDING_REVIEW")] == [str(draft_id)]

    r = await api.get(f"/review/drafts/{draft_id}/pages", headers=h)
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["document_url"] == BSF_URL
    expected = extract_pages(PDF.read_bytes())
    assert [(p["page_number"], p["text"], p["ocr"]) for p in doc["pages"]] == [(p.page_number, p.text, p.ocr) for p in expected]
    texts = {p["page_number"]: p["text"] for p in doc["pages"]}

    payload = json.loads((await db_session.get(ExtractionDraft, draft_id)).payload)
    accepted = {(vi, name) for vi, v in enumerate(payload["vacancies"]) for name, f in v["fields"].items() if f["accepted"]}
    spans = {(s["vacancy_index"], s["field"]): s for s in doc["spans"]}
    assert accepted and accepted <= set(spans)
    for key in accepted:
        s = spans[key]
        assert s["located"] and s["accepted"], key
        # The highlighted text IS the quote the validator matched, on the page it was found on
        assert _ws(texts[s["page"]][s["start"]:s["end"]]) == _ws(s["quote"]) == _ws(payload["vacancies"][key[0]]["fields"][key[1]]["quote"])
    post = spans[(0, "post_name")]
    assert post["page"] == 1 and "Specialist Doctors" in texts[1][post["start"]:post["end"]]
    edu = spans[(0, "essential_qualification")]
    # Offsets are into the page text as extracted (line breaks kept), so the console can show the page as printed
    assert texts[edu["page"]][edu["start"]:edu["end"]].startswith("(i)\nA recognized medical qualification")
    # A quote the validator could not find in the document is listed but never highlighted
    for s in doc["spans"]:
        if s["reason"] == "quote not found in document":
            assert not s["located"] and s["start"] is None

    # Approve one vacancy (one draft, after looking at it), then bulk-reject what is left: both land in the audit log
    r = await api.post(f"/review/drafts/{draft_id}/approve", json={"vacancy_indexes": [0], "note": "checked page 1"}, headers=h)
    assert r.status_code == 200, r.text
    assert (await api.post("/review/drafts/bulk-reject", json={"draft_ids": [str(draft_id)], "note": " "}, headers=h)).status_code == 422
    r = await api.post("/review/drafts/bulk-reject", json={"draft_ids": [str(draft_id)], "note": "GDMO post name not verified"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == {"rejected": 1, "status": {str(draft_id): "APPROVED"}}  # one vacancy was approved before
    assert (await api.post("/review/drafts/bulk-reject", json={"draft_ids": [str(draft_id)], "note": "again"}, headers=h)).status_code == 409
    audit = (await api.get("/admin/audit", params={"object_type": "DRAFT", "object_id": str(draft_id)}, headers=h)).json()["items"]
    assert [(a["action"], a["note"]) for a in audit] == [("DRAFT_REJECT", "GDMO post name not verified"), ("DRAFT_APPROVE", "checked page 1")]
    assert len(audit[1]["detail"]["created_vacancy_ids"]) == 1
    assert all(i["type"] == "NOTICE" for i in await _all_items(api, h))

@pytest.mark.asyncio
async def test_queue_follows_triage_and_hides_stale_and_not_required(api, login, db_session):
    """Integration of the triage module with the console, on the 282 real notices."""
    from govcareeros.backend.models import NoticeTriage
    from govcareeros.backend.services.triage import triage_notices

    await _load_snapshot(db_session)
    h = await login("rev@example.com")
    await _make_reviewer(db_session, "rev@example.com")
    before = await _all_items(api, h, type="NOTICE")

    # Triage as of the snapshot's capture day, so ageing is reproducible
    captured = datetime.fromisoformat(json.loads((FIXTURES / "notices_snapshot.json").read_text(encoding="utf-8"))["meta"]["captured_at"])
    await triage_notices(db_session, now=captured, apply=True)
    await db_session.commit()
    triaged = {t.notice_id: t for t in (await db_session.execute(select(NoticeTriage))).scalars()}
    out = {nid for nid, t in triaged.items() if t.triage_status in ("STALE", "NOT_REQUIRED")}
    assert out, "the snapshot contains RESULT/RULES/OTHER notices, so some must leave the queue"

    after = await _all_items(api, h, type="NOTICE")
    ids = [uuid.UUID(i["id"]) for i in after]
    assert not out & set(ids) and len(after) == len(before) - len(out & {uuid.UUID(i["id"]) for i in before})
    # Highest triage priority first
    prios = [triaged[i].priority for i in ids if i in triaged]
    assert prios == sorted(prios, reverse=True)
    # Triaged-out notices are still listable explicitly (never deleted)
    nr = [n for n in SNAPSHOT if n["notice_kind"] in ("RESULT", "RULES", "OTHER") and n["review_status"] == "MANUAL_REVIEW"]
    assert nr and (await api.get("/review/queue", params={"status": "MANUAL_REVIEW", "limit": 200}, headers=h)).json()["total"] >= len(nr)
