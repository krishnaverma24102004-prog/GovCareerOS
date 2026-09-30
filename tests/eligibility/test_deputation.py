"""
Recruitment mode + deputation eligibility on real notices:
  - MHA vacancy circular (Custodian of Enemy Property, Inspector on deputation), a scanned PDF;
    its OCR text is stored next to it (tests/fixtures/mha) so the test does not need Tesseract
  - UPSC Advt 11/2026 and 12/2026 (direct recruitment)
"""
import json
from pathlib import Path

import pytest

from govcareeros.backend.documents.pdf_text import PageText, extract_pages
from govcareeros.backend.eligibility.rules import ProfileSnapshot, Requirement, check_govt_service, overall_status
from govcareeros.backend.parsing.recruitment_mode import detect_in_pages, detect_recruitment_mode, govt_service_requirement
from govcareeros.backend.parsing.upsc_advertisement import parse_advertisement
from govcareeros.backend.services.extraction_drafts import recruitment_mode

FIXTURES = Path(__file__).parents[1] / "fixtures"

def _mha_pages():
    data = json.loads((FIXTURES / "mha" / "CircularVacancy_03082026.ocr.json").read_text(encoding="utf-8"))
    return [PageText(p["page_number"], p["text"], p["ocr"]) for p in data["pages"]]

def test_mha_circular_is_deputation_for_central_government_officers_only():
    found = detect_in_pages(_mha_pages())
    assert found.mode == "DEPUTATION" and found.page == 1
    assert "deputation basis" in found.excerpt
    # "exclusively by officers currently working under the Central Government. State Government employees ... not eligible"
    assert found.open_to == ("CENTRAL",)
    assert govt_service_requirement(found) == {"mode": "DEPUTATION", "open_to": ["CENTRAL"]}

def test_draft_approval_reads_mode_from_notice_text():
    # No verified quote states the mode, so the notice's own text decides
    found, section = recruitment_mode({}, _mha_pages())
    assert found.mode == "DEPUTATION" and found.page == 1 and section.startswith("notice text")

@pytest.mark.parametrize("pdf", ["AdvtNo-11-2026-Engl-100926.pdf", "AdvtNo-12-2026-Engl-250926_0.pdf"])
def test_upsc_selection_posts_are_direct_recruitment(pdf):
    parsed = parse_advertisement(extract_pages((FIXTURES / "upsc" / pdf).read_bytes()))
    modes = {v.external_ref: v.fields["recruitment_mode"] for v in parsed.vacancies}
    assert {f.value for f in modes.values()} == {"DIRECT"}
    assert all("DIRECT RECRUITMENT" in f.provenance.excerpt.upper() for f in modes.values())
    assert not any(r.req_type == "GOVT_SERVICE" for v in parsed.vacancies for r in v.requirements)

def test_contract_wording_in_an_age_relaxation_clause_is_not_a_contract_post():
    # Advt 11/2026, Assistant Public Prosecutor block: "Age relaxation to contractual employees ..."
    text = "\n".join(p.text for p in extract_pages((FIXTURES / "upsc" / "AdvtNo-11-2026-Engl-100926.pdf").read_bytes()))
    start = text.index("Age relaxation to contractual employees")
    assert detect_recruitment_mode(text[start:start + 1500]) is None

def _profile(central, state):
    return ProfileSnapshot(None, None, None, None, None, is_central_govt_employee=central, is_state_govt_employee=state)

@pytest.mark.parametrize("central,state,outcome,rule", [
    (True, None, "CONDITIONAL", "serving Central Government employee"),
    (False, True, "NOT_MET", "State Government employees are not eligible for this post"),
    (False, False, "NOT_MET", "profile: not a government employee"),
    (None, None, "UNKNOWN", "government employment not stated in profile"),
])
def test_govt_service_check_on_the_mha_requirement(central, state, outcome, rule):
    found = detect_in_pages(_mha_pages())
    req = Requirement("r1", "GOVT_SERVICE", govt_service_requirement(found), found.page)
    [o] = check_govt_service(_profile(central, state), [req])
    assert (o.outcome, o.rule, o.source_page) == (outcome, rule, 1)
    assert o.requirement == "deputation: Central Government officers only"
    assert overall_status([o])[0] == {"CONDITIONAL": "CONDITIONAL", "NOT_MET": "INELIGIBLE", "UNKNOWN": "MANUAL_REVIEW"}[outcome]

def test_direct_recruitment_adds_no_govt_service_check():
    assert check_govt_service(_profile(False, False), [], "DIRECT") == []
    # A DEPUTATION mode without its requirement row: whose officers may apply is unknown, a State employee stays open
    [o] = check_govt_service(_profile(False, True), [], "DEPUTATION")
    assert o.outcome == "UNKNOWN"

@pytest.mark.asyncio
async def test_vacancy_list_filters_by_recruitment_mode(api, login, db_session):
    from govcareeros.backend.services.document_ingestion import get_or_create_source, ingest_pdf_bytes
    from govcareeros.backend.sources import upsc
    source = await get_or_create_source(db_session, upsc.UPSC_SOURCE)
    name = "AdvtNo-12-2026-Engl-250926_0.pdf"
    await ingest_pdf_bytes(db_session, source, f"https://www.upsc.gov.in/sites/default/files/{name}", (FIXTURES / "upsc" / name).read_bytes())
    await db_session.commit()
    h = await login("filter@example.com")
    direct = (await api.get("/vacancies", params={"recruitment_mode": "DIRECT"}, headers=h)).json()
    assert direct and {v["recruitment_mode"] for v in direct} == {"DIRECT"}
    assert (await api.get("/vacancies", params={"recruitment_mode": "DEPUTATION"}, headers=h)).json() == []
    detail = (await api.get(f"/vacancies/{direct[0]['id']}", headers=h)).json()
    assert any(p["field_name"] == "recruitment_mode" and p["value"] == "DIRECT" for p in detail["provenance"])
