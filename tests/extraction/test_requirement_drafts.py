"""
Requirements draft path without an LLM call: no model output has been recorded for the requirements prompt yet,
so the located items below are HAND-BUILT, with quotes copied verbatim from real notices: the MHA vacancy circular
(scanned; its stored OCR text, tests/fixtures/mha) and SSC CGL 2026, plus the fee sentences of UPSC Advt 12/2026
(page 21, https://www.upsc.gov.in/sites/default/files/AdvtNo-12-2026-Engl-250926_0.pdf) and the UPSC IES/ISS 2026
examination notice (page 9, https://www.upsc.gov.in/sites/default/files/Exam_Notification_IES_ISS_Eng_11022026.pdf).
Everything after that (validator verdicts, draft payload, reviewer approval, ApplicationRequirement rows, the
deterministic re-parse next to approved rows, quote highlighting) runs through the production code.
"""
import dataclasses
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from govcareeros.backend.documents.pdf_text import PageText, extract_pages
from govcareeros.backend.extraction.requirements_extractor import verify_all
from govcareeros.backend.extraction.validator import verify_requirement
from govcareeros.backend.llm.provider import LLMResponse
from govcareeros.backend.models import ApplicationRequirement, ExtractionDraft, RawDocument, User
from govcareeros.backend.parsing.ssc_application import parse_ssc_application_info
from govcareeros.backend.services.application_prep import merge_sources, store_application_requirements
from govcareeros.backend.services.document_ingestion import store_raw_document
from govcareeros.backend.services.extraction_drafts import DraftError
from govcareeros.backend.services.requirement_drafts import approve_requirements_draft, create_requirements_draft
from govcareeros.backend.services.review_console import quote_spans
from govcareeros.backend.services.source_discovery import sync_registry
from govcareeros.backend.sources.registry import SOURCES

FIXTURES = Path(__file__).parents[1] / "fixtures"
MHA_PDF = FIXTURES / "mha" / "CircularVacancy_03082026.pdf"
MHA_URL = "https://www.mha.gov.in/sites/default/files/CircularVacancy_03082026.pdf"
CGL = FIXTURES / "ssc" / "Notice_of_adv_cgl_2026.pdf"
CGL_URL = "https://ssc.gov.in/api/attachment/uploads/masterData/NoticeBoards/Notice_of_adv_cgl_2026.pdf"
ADVT12 = FIXTURES / "upsc" / "AdvtNo-12-2026-Engl-250926_0.pdf"
IES = FIXTURES / "upsc" / "prep" / "Exam_Notification_IES_ISS_Eng_11022026.pdf"

VIGILANCE = "(b) Vigilance Clearance Certificate."
MHA_LOCATED = [
    {"req_type": "KEY_DATE", "code": "APPLICATION_END", "value": "28.08.2026", "page": 2,
     "quote": "Willing and eligible officers must forward their applications through their respective Ministries/Departments/Cadre "
              "Controlling Authorities onlor before 28.08.2026 by 5.00 PM."},
    {"req_type": "DOCUMENT", "code": "DOCUMENT", "value": "Detailed bio-data", "page": 2,
     "quote": "(a) Detailed bio-data in the prescribed proforma (see Annexure)."},
    {"req_type": "DOCUMENT", "code": "DOCUMENT", "value": "Vigilance Clearance Certificate", "page": 2, "quote": VIGILANCE},
    {"req_type": "DOCUMENT", "code": "DOCUMENT", "value": "ACRs/APARs", "page": 2,
     "quote": "(c) Certified copies of Annual Confidential Reports (ACRs) / Annual Performance Appraisal Reports (APARs) for the last five (05) years."},
    {"req_type": "DOCUMENT", "code": "DOCUMENT", "value": "No Objection Certificate", "page": 2,
     "quote": "(d) No Objection Certificate from the Employer/ Cadre Controlling|Authority."},
    # A real quote cited on the wrong page, and a document the circular does not ask for
    {"req_type": "DOCUMENT", "code": "DOCUMENT", "value": "Vigilance Clearance Certificate", "page": 1, "quote": VIGILANCE},
    {"req_type": "DOCUMENT", "code": "DOCUMENT", "value": "Medical certificate", "page": 2,
     "quote": "(e) Medical fitness certificate from a Civil Surgeon."},
]

CGL_EWS = ("a person, seeking appointment on the basis of reservation to EWS Category, must ensure that he possesses the Income & Asset "
           "certificate valid for the Financial Year 2026-2027 issued on the basis of Income for the Financial Year 2025-2026")
CGL_SIGNATURE = ("Candidates are required to upload the scanned signature in JPEG/JPG format (10 to 20 KB). Image dimension of the "
                 "signature should be about 6.0 cm (width) x 2.0 cm (height).")
CGL_EXEMPT = ("Women candidates and candidates belonging to Scheduled Castes (SC), Scheduled Tribes (ST), Persons with Benchmark "
              "Disabilities (PwBD) and Ex-servicemen (ESM) eligible for reservation are exempted from payment of fee.")
CGL_LOCATED = [
    {"req_type": "KEY_DATE", "code": "APPLICATION_END", "value": "22.06.2026", "page": 1,
     "quote": "Last date and time for receipt of online applications 22.06.2026 (23:00 hours)"},
    {"req_type": "CERTIFICATE", "code": "OBC_NCL", "value": "non-creamy layer on the closing date", "page": 11,
     "quote": "A person seeking appointment on the basis of reservation to OBC must ensure that he does not fall in creamy layer on the "
              "crucial date i.e. the closing date of application."},
    {"req_type": "CERTIFICATE", "code": "EWS", "value": "income of Financial Year 2025-2026", "page": 11, "quote": CGL_EWS},
    {"req_type": "UPLOAD_RULE", "code": "SIGNATURE", "value": "JPEG/JPG, 10 to 20 KB, 6.0 cm x 2.0 cm", "page": 18, "quote": CGL_SIGNATURE},
    {"req_type": "FEE", "code": "APPLICATION_FEE", "value": "100", "page": 19, "quote": "Fee payable: ₹100/- (Rs one hundred only)."},
    {"req_type": "FEE_EXEMPTION", "code": "FEE_EXEMPT", "value": "Women, SC, ST, PwBD, ESM", "page": 19, "quote": CGL_EXEMPT},
]

def _mha_pages():
    data = json.loads((FIXTURES / "mha" / "CircularVacancy_03082026.ocr.json").read_text(encoding="utf-8"))
    return [PageText(p["page_number"], p["text"], p["ocr"]) for p in data["pages"]]

@pytest.fixture(scope="module")
def cgl_pages():
    return extract_pages(CGL.read_bytes(), ocr=False)

def test_quotes_must_be_on_the_cited_page():
    verdicts = verify_all(MHA_LOCATED, _mha_pages())
    accepted = [(r.req_type, r.code, r.applies_when, r.page) for r in verdicts if r.accepted]
    assert accepted == [("KEY_DATE", "APPLICATION_END", "ALWAYS", 2), ("DOCUMENT", "BIO_DATA", "ALWAYS", 2),
                        ("DOCUMENT", "VIGILANCE_CLEARANCE", "ALWAYS", 2), ("DOCUMENT", "ACR_APAR", "ALWAYS", 2),
                        ("DOCUMENT", "NO_OBJECTION_CERTIFICATE", "GOVT_SERVICE", 2)]
    assert verdicts[0].value == {"at": "2026-08-28T17:00:00+05:30"}  # "by 5.00 PM"
    assert verdicts[2].value == {"text": VIGILANCE} and verdicts[2].match == "EXACT"
    rejected = [r.reason for r in verdicts if not r.accepted]
    assert rejected == ["quote is on page 2, not on the cited page 1", "quote not found on the cited page 2"]

def test_values_are_derived_from_the_quote(cgl_pages):
    verdicts = {(r.req_type, r.code): r for r in verify_all(CGL_LOCATED, cgl_pages)}
    assert all(r.accepted for r in verdicts.values()), {k: r.reason for k, r in verdicts.items()}
    # "the closing date" resolves to the verified APPLICATION_END of the same draft
    obc = verdicts[("CERTIFICATE", "OBC_NCL")].value
    assert (obc["crucial_date"], obc["source_page"], obc["clauses"][0]["page"], obc["kind"]) == ("2026-06-22", 11, 11, "OBC_NCL")
    ews = verdicts[("CERTIFICATE", "EWS")]
    assert (ews.value["financial_year"], ews.value["crucial_date"], ews.applies_when) == ("2025-26", None, "CATEGORY_EWS")
    sig = verdicts[("UPLOAD_RULE", "SIGNATURE")].value
    assert (sig["formats"], sig["min_kb"], sig["max_kb"], sig["width_cm"], sig["height_cm"]) == (["JPEG", "JPG"], 10, 20, 6.0, 2.0)
    assert verdicts[("FEE", "APPLICATION_FEE")].value == {"amount": 100, "currency": "INR", "category": None}
    assert verdicts[("FEE_EXEMPTION", "FEE_EXEMPT")].value["exempt"] == ["ESM", "FEMALE", "PWBD", "SC", "ST"]

@pytest.mark.parametrize("item,reason", [
    ({**CGL_LOCATED[2], "value": "income of 2024-25"}, "'income of 2024-25' disagrees with the financial year in the quote (2025-26)"),
    ({**CGL_LOCATED[3], "value": "10 to 50 KB"}, "'10 to 50 KB' has a number the quote does not state"),
    ({**CGL_LOCATED[4], "value": "200"}, "'200' is not a rupee amount in the quote"),
    ({**CGL_LOCATED[4], "code": "APPLICATION_FEE_OBC"}, "category 'OBC' is not named in the quote"),
    # The exemption sentence names SC, but it is printed on page 19, not page 11
    ({"req_type": "CERTIFICATE", "code": "SC", "value": "SC", "page": 11, "quote": CGL_EXEMPT}, "quote is on page 19, not on the cited page 11"),
    ({"req_type": "CERTIFICATE", "code": "EWS", "value": None, "page": 19, "quote": CGL_EXEMPT}, "quote does not name a EWS certificate"),
    ({"req_type": "UPLOAD_RULE", "code": "PHOTOGRAPH", "value": None, "page": 18, "quote": CGL_SIGNATURE},
     "no photograph format, size, dimension or background in the quote"),
    ({"req_type": "PAYMENT", "code": "UPI", "value": None, "page": 20, "quote": "Fee can be paid online through BHIM UPI"}, "unknown requirement type 'PAYMENT'"),
])
def test_tampered_items_are_rejected(cgl_pages, item, reason):
    v = verify_requirement(item, cgl_pages)
    assert not v.accepted and v.reason == reason

class HandBuilt:
    """Stands in for the model: returns the hand-built located items above. It is not model output."""
    name, model, chunk_chars = "none", "hand-built", 120_000

    def __init__(self, items):
        self.items = items

    async def complete_json(self, system, user, schema):
        return LLMResponse({"requirements": self.items}, self.model, 0.0)

@pytest.mark.asyncio
async def test_reviewer_approves_named_items_only(api, login, db_session):
    source = (await sync_registry(db_session, [s for s in SOURCES if s.name == "MHA"]))["MHA"]
    raw, _ = await store_raw_document(db_session, source, MHA_URL, MHA_PDF.read_bytes(), 200, "application/pdf", discovery_method="OCR")
    draft = await create_requirements_draft(db_session, source, raw, _mha_pages(), HandBuilt(MHA_LOCATED))
    assert (draft.status, draft.fields_offered, draft.fields_accepted, draft.prompt_version) == ("PENDING_REVIEW", 7, 5, "requirements_locate/1")
    payload = json.loads(draft.payload)
    assert payload["kind"] == "requirements" and payload["vacancies"] == []
    await db_session.commit()
    draft_id = draft.id

    h = await login("reviewer@example.com")
    assert (await api.post(f"/review/drafts/{draft_id}/approve", json={"requirement_indexes": [0]}, headers=h)).status_code == 403
    (await db_session.execute(select(User).where(User.email == "reviewer@example.com"))).scalar_one().is_reviewer = True
    await db_session.commit()
    # No approve-all, and an item the validator rejected can never be approved
    r = await api.post(f"/review/drafts/{draft_id}/approve", json={}, headers=h)
    assert r.status_code == 409 and "nothing is approved in bulk" in r.json()["detail"]
    r = await api.post(f"/review/drafts/{draft_id}/approve", json={"requirement_indexes": [0, 5]}, headers=h)
    assert r.status_code == 409 and "not verified (quote is on page 2, not on the cited page 1)" in r.json()["detail"]
    assert (await db_session.execute(select(ApplicationRequirement))).scalars().all() == []

    r = await api.post(f"/review/drafts/{draft_id}/approve", json={"requirement_indexes": [0, 2], "note": "checked p.2"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "PENDING_REVIEW"  # three verified items are still undecided
    rows = {x.code: x for x in (await db_session.execute(select(ApplicationRequirement))).scalars()}
    assert set(rows) == {"APPLICATION_END", "VIGILANCE_CLEARANCE"}
    vig = rows["VIGILANCE_CLEARANCE"]
    assert (vig.req_type, vig.applies_when, vig.source_page, vig.source_excerpt) == ("DOCUMENT", "ALWAYS", 2, VIGILANCE)
    assert vig.parser_version == "llm:requirements_locate/1:hand-built" and vig.source_section == "LLM-located (EXACT), reviewer-approved"
    assert json.loads(rows["APPLICATION_END"].value) == {"at": "2026-08-28T17:00:00+05:30"}
    assert vig.raw_document_id == (await db_session.execute(select(RawDocument.id))).scalar_one()

    r = await api.post(f"/review/drafts/{draft_id}/approve", json={"requirement_indexes": [2]}, headers=h)
    assert r.status_code == 409 and "already APPROVED" in r.json()["detail"]
    r = await api.post(f"/review/drafts/{draft_id}/reject", json={"note": "rest not needed"}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "APPROVED"
    decisions = [x.get("decision", {}).get("status") for x in r.json()["payload"]["requirements"]]
    assert decisions == ["APPROVED", "REJECTED", "APPROVED", "REJECTED", "REJECTED", None, None]
    assert len((await db_session.execute(select(ApplicationRequirement))).scalars().all()) == 2
    assert (await db_session.get(ExtractionDraft, draft_id)).status == "APPROVED"

@pytest.mark.asyncio
async def test_closing_date_is_approved_with_the_certificate_that_uses_it(api, login, db_session, cgl_pages):
    """"on the crucial date i.e. the closing date" (CGL 2026, page 11): the date itself is the draft's APPLICATION_END
    item (page 1), so the OBC row cannot be stored without it; the EWS row states its own year and needs nothing."""
    from govcareeros.backend.services.document_ingestion import get_or_create_source
    from govcareeros.backend.sources import ssc
    source = await get_or_create_source(db_session, ssc.SSC_SOURCE)
    raw, _ = await store_raw_document(db_session, source, "https://ssc.gov.in/api/attachment/uploads/masterData/NoticeBoards/Notice_of_adv_cgl_2026.pdf",
                                      CGL.read_bytes(), 200, "application/pdf")
    draft = await create_requirements_draft(db_session, source, raw, cgl_pages, HandBuilt(CGL_LOCATED))
    items = json.loads(draft.payload)["requirements"]
    idx = {(r["req_type"], r["code"]): i for i, r in enumerate(items)}
    end, obc, ews = idx[("KEY_DATE", "APPLICATION_END")], idx[("CERTIFICATE", "OBC_NCL")], idx[("CERTIFICATE", "EWS")]
    draft_id = draft.id
    await db_session.commit()
    h = await login("reviewer@example.com")
    (await db_session.execute(select(User).where(User.email == "reviewer@example.com"))).scalar_one().is_reviewer = True
    await db_session.commit()

    r = await api.post(f"/review/drafts/{draft_id}/approve", json={"requirement_indexes": [obc]}, headers=h)
    assert r.status_code == 409 and "approve the APPLICATION_END key date with it" in r.json()["detail"]
    r = await api.post(f"/review/drafts/{draft_id}/approve", json={"requirement_indexes": [ews]}, headers=h)
    assert r.status_code == 200, r.text
    r = await api.post(f"/review/drafts/{draft_id}/approve", json={"requirement_indexes": [end, obc]}, headers=h)
    assert r.status_code == 200, r.text
    rows = {x.code: json.loads(x.value) for x in (await db_session.execute(select(ApplicationRequirement))).scalars()}
    assert rows["OBC_NCL"]["crucial_date"] == "2026-06-22" and rows["APPLICATION_END"] == {"at": "2026-06-22T23:00:00+05:30"}

# ---------- a category fee must be the amount the quote charges that category ----------

# CGL 2026 page 19: the fee, then the exemption, in one quote
CGL_FEE = ("10.1 Fee payable: ₹100/- (Rs one hundred only). 10.2 Women candidates and candidates belonging to Scheduled Castes (SC), "
           "Scheduled Tribes (ST), Persons with Benchmark Disabilities (PwBD) and Ex-servicemen (ESM) eligible for reservation are "
           "exempted from payment of fee.")
# UPSC Advt 12/2026 page 21 and IES/ISS 2026 page 9: the charging sentence names the exempted groups
UPSC_FEE = ("(a) Candidates (Except Female/SC/ST/Persons with Benchmark Disability Candidates who are exempted from payment of fee) "
            "are required to pay a fee of Rs. 25/- (Rupees Twenty five)")
IES_FEE = ("Candidates (except Female/SC/ST/Persons with Benchmark Disability (PwBD) Candidates who are exempted from payment of fee) "
           "are required to pay fee of Rs. 200/- (Rupees Two Hundred only)")

@pytest.fixture(scope="module")
def fee_pages(cgl_pages):
    return {"cgl": cgl_pages, "advt12": extract_pages(ADVT12.read_bytes(), ocr=False), "ies": extract_pages(IES.read_bytes(), ocr=False)}

@pytest.mark.parametrize("doc,code,value,page,quote,reason", [
    ("cgl", "APPLICATION_FEE_SC", "100", 19, CGL_FEE, "the quote does not charge SC candidates Rs. 100"),
    ("cgl", "APPLICATION_FEE_FEMALE", "100", 19, CGL_FEE, "the quote does not charge FEMALE candidates Rs. 100"),
    ("advt12", "APPLICATION_FEE_SC", "25", 21, UPSC_FEE, "the quote does not charge SC candidates Rs. 25"),
    ("advt12", "APPLICATION_FEE_FEMALE", "25", 21, UPSC_FEE, "the quote does not charge FEMALE candidates Rs. 25"),
    ("ies", "APPLICATION_FEE_PWBD", "200", 9, IES_FEE, "the quote does not charge PWBD candidates Rs. 200"),
])
def test_category_fee_must_be_tied_to_its_amount(fee_pages, doc, code, value, page, quote, reason):
    """Each quote names the category and prints the amount, but only to exempt that category from it."""
    v = verify_requirement({"req_type": "FEE", "code": code, "value": value, "page": page, "quote": quote}, fee_pages[doc])
    assert v.quote and not v.accepted and v.reason == reason

@pytest.mark.parametrize("doc,value,page,quote", [("cgl", 100, 19, CGL_FEE), ("advt12", 25, 21, UPSC_FEE), ("ies", 200, 9, IES_FEE)])
def test_the_same_quotes_give_the_general_fee(fee_pages, doc, value, page, quote):
    v = verify_requirement({"req_type": "FEE", "code": "APPLICATION_FEE", "value": str(value), "page": page, "quote": quote}, fee_pages[doc])
    assert v.accepted and v.value == {"amount": value, "currency": "INR", "category": None}, v.reason

# ---------- a repeated (req_type, code) is shown as a duplicate, not dropped ----------

CGL_FEE_P50 = {"req_type": "FEE", "code": "APPLICATION_FEE", "value": "100", "page": 50,
               "quote": "Fee payable: ₹ 100/- (Rupees one hundred only)."}
# Page 20: a correction charge, which the validator cannot tell from a fee; the reviewer can
CGL_CORRECTION = {"req_type": "FEE", "code": "APPLICATION_FEE", "value": "200", "page": 20,
                  "quote": "The Commission will levy a uniform correction charge of ₹ 200/- for making correction(s) and re-submitting "
                           "modified/ corrected application for the first time"}

def test_second_accepted_item_of_a_kind_is_a_duplicate(cgl_pages):
    verdicts = verify_all(CGL_LOCATED + [CGL_FEE_P50, CGL_CORRECTION, CGL_LOCATED[4]], cgl_pages)
    fees = [v for v in verdicts if v.req_type == "FEE"]
    # The verbatim repeat of the page 19 item is the same item; the other two stay, accepted and marked
    assert [(v.page, v.accepted, v.duplicate_of) for v in fees] == [
        (19, True, None),
        (50, True, "FEE APPLICATION_FEE on page 19 (same value)"),
        (20, True, "FEE APPLICATION_FEE on page 19 (different value: {'amount': 100, 'currency': 'INR', 'category': None} there)"),
    ]
    assert all(v.duplicate_of is None for v in verdicts if v.req_type != "FEE")

async def _reviewer(db_session):
    user = User(email="reviewer@example.com", hashed_password="test-only", is_reviewer=True)
    db_session.add(user)
    await db_session.flush()
    return user

async def _cgl_doc(db_session):
    from govcareeros.backend.services.document_ingestion import get_or_create_source
    from govcareeros.backend.sources import ssc
    source = await get_or_create_source(db_session, ssc.SSC_SOURCE)
    raw, _ = await store_raw_document(db_session, source, CGL_URL, CGL.read_bytes(), 200, "application/pdf")
    return source, raw

@pytest.mark.asyncio
async def test_reviewer_approves_one_of_two_duplicates(db_session, cgl_pages):
    source, raw = await _cgl_doc(db_session)
    draft = await create_requirements_draft(db_session, source, raw, cgl_pages, HandBuilt([CGL_LOCATED[4], CGL_CORRECTION]))
    items = json.loads(draft.payload)["requirements"]
    assert [(r["page"], r["accepted"], bool(r["duplicate_of"])) for r in items] == [(19, True, False), (20, True, True)]
    reviewer = await _reviewer(db_session)
    with pytest.raises(DraftError, match="items 0 and 1 are both FEE APPLICATION_FEE; approve one of them"):
        await approve_requirements_draft(db_session, draft, reviewer, [0, 1])
    assert (await db_session.execute(select(ApplicationRequirement))).scalars().all() == []
    [row] = await approve_requirements_draft(db_session, draft, reviewer, [0])
    assert (row.source_page, json.loads(row.value)["amount"]) == (19, 100)
    with pytest.raises(DraftError, match="document already has a FEE APPLICATION_FEE requirement"):
        await approve_requirements_draft(db_session, draft, reviewer, [1])

# ---------- a deterministic parse never deletes reviewer-approved rows ----------

@pytest.mark.asyncio
async def test_deterministic_parse_keeps_approved_rows(db_session, cgl_pages):
    """CGL 2026 approved from an LLM draft first, then parsed by parsing/ssc_application.py."""
    source, raw = await _cgl_doc(db_session)
    draft = await create_requirements_draft(db_session, source, raw, cgl_pages, HandBuilt(CGL_LOCATED))
    approved = await approve_requirements_draft(db_session, draft, await _reviewer(db_session), list(range(len(CGL_LOCATED))))
    llm = {(r.req_type, r.code): r.id for r in approved}
    assert len(llm) == 6

    info = await store_application_requirements(db_session, source, raw, cgl_pages, "CGL 2026", parse=parse_ssc_application_info)
    rows = (await db_session.execute(select(ApplicationRequirement))).scalars().all()
    assert {(r.req_type, r.code): r.id for r in rows if r.parser_version.startswith("llm:")} == llm
    det = [r for r in rows if r.parser_version == "ssc_application/1"]
    assert len(det) == len(parse_ssc_application_info(cgl_pages).items)
    # The parse reads the format annexure from the next sentence; the approved quotes stop before it
    conflicts = [w for w in info.warnings if "differ in" in w]
    assert sorted(conflicts) == [
        "CERTIFICATE EWS: the ssc_application/1 row (page 11) and the llm:requirements_locate/1:hand-built row (page 11) differ in "
        "format_note; both are kept, the official notice prevails",
        "CERTIFICATE OBC_NCL: the ssc_application/1 row (page 11) and the llm:requirements_locate/1:hand-built row (page 11) differ in "
        "format_note; both are kept, the official notice prevails",
    ]

    # The checklist reads one row per agreeing pair (the parse's), both rows where they differ
    merged, warnings = merge_sources(rows)
    assert sorted(warnings) == sorted(conflicts)
    by_key = {}
    for r in merged:
        by_key.setdefault((r.req_type, r.code), []).append(r.parser_version)
    assert by_key[("FEE", "APPLICATION_FEE")] == ["ssc_application/1"]  # {"amount": 100} both ways
    assert by_key[("KEY_DATE", "APPLICATION_END")] == ["ssc_application/1"]
    assert by_key[("CERTIFICATE", "OBC_NCL")] == ["ssc_application/1", "llm:requirements_locate/1:hand-built"]

    # A newer version of the same parser replaces only its own rows
    newer = lambda pages: dataclasses.replace(parse_ssc_application_info(pages), parser_version="ssc_application/2")
    await store_application_requirements(db_session, source, raw, cgl_pages, "CGL 2026", parse=newer)
    after = (await db_session.execute(select(ApplicationRequirement))).scalars().all()
    assert sorted({r.parser_version for r in after}) == ["llm:requirements_locate/1:hand-built", "ssc_application/2"]
    assert len(after) == len(rows)

# ---------- the review console highlights requirement quotes ----------

def test_requirement_quotes_are_highlighted(cgl_pages):
    items = CGL_LOCATED + [{"req_type": "FEE", "code": "APPLICATION_FEE_SC", "value": "100", "page": 19, "quote": CGL_FEE},
                           {**CGL_LOCATED[3], "page": 17}]
    payload = {"kind": "requirements", "vacancies": [], "requirements": [dataclasses.asdict(v) for v in verify_all(items, cgl_pages)]}
    spans = quote_spans(payload, cgl_pages)
    text = {p.page_number: p.text for p in cgl_pages}
    by_field = {s["field"]: s for s in spans}
    # Every verified item, at its own words on its own page
    for i, r in enumerate(payload["requirements"]):
        if r["accepted"]:
            s = by_field[f"{r['req_type']} {r['code']}"]
            assert s["located"] and s["vacancy_index"] is None and s["requirement_index"] == i
            assert " ".join(text[s["page"]][s["start"]:s["end"]].split()) == " ".join(r["quote"].split())
    # A quote found but refused (the fee does not charge SC) is shown as refused; one not on its cited page has no span
    sc = by_field["FEE APPLICATION_FEE_SC"]
    assert (sc["accepted"], sc["located"], sc["page"], sc["reason"]) == (False, True, 19, "the quote does not charge SC candidates Rs. 100")
    assert len(spans) == len(items) - 1
