"""
Post-wise tables of real SSC notices, fetched from ssc.gov.in through backend/sources/ssc.py on 2026-10-01
(URLs and capture times in tests/fixtures/ssc/sources.json):

  Notice_of_adv_cgl_2026.pdf  "Notice of Combined Graduate Level Examination, 2026" (132 pages)
  Notice_of_adv_je_2026.pdf   "Notice of Junior Engineer Examination, 2026" (97 pages)

Expected values are read off the PDFs by eye, e.g. CGL page 3, para 2.3 row 7:
"Junior Statistical Officer | Ministry of Statistics & Programme Implementation. | Group "B" | 18-32 years".
"""
import json
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select

from govcareeros.backend.documents.pdf_text import extract_pages
from govcareeros.backend.models import Exam, ExamPost, ExamRequirement
from govcareeros.backend.parsing.ssc_tables import parse_exam_pdf, parse_post_tables
from govcareeros.backend.parsing.upsc_advertisement import IST
from govcareeros.backend.services.document_ingestion import get_or_create_source
from govcareeros.backend.services.exam_ingestion import ingest_exam_pdf
from govcareeros.backend.sources import ssc

SSC = Path(__file__).parents[1] / "fixtures" / "ssc"
SOURCES = json.loads((SSC / "sources.json").read_text(encoding="utf-8"))
CGL, JE = "Notice_of_adv_cgl_2026.pdf", "Notice_of_adv_je_2026.pdf"

def _tables(name):
    body = (SSC / name).read_bytes()
    return parse_post_tables(body, extract_pages(body, ocr=False))

def _post(posts, name, department=None, level=None):
    hits = [p for p in posts if p.post_name == name and (department is None or p.department == department)
            and (level is None or p.pay_level == level)]
    assert len(hits) == 1, [(p.post_name, p.department, p.pay_level) for p in posts if p.post_name == name]
    return hits[0]

def test_cgl_post_rows_with_pages_pay_levels_and_merged_cells():
    t = _tables(CGL)
    assert t.warnings == []
    assert len(t.posts) == 40
    levels = [p.pay_level for p in t.posts]
    assert [levels.count(f"Level-{n}") for n in (8, 7, 6, 5, 4)] == [3, 15, 10, 6, 6]

    aao = _post(t.posts, "Assistant Audit Officer (Central Cadre)")
    assert (aao.pay_level, aao.pay_scale, aao.age_min, aao.age_max, aao.provenance.page) == \
        ("Level-8", "Pay Level-8 (₹ 47600 to 151100)", 18, 30, 2)
    assert aao.classification == "Group “B” Gazetted (Non-Ministerial)"
    assert aao.provenance.section == "Details of the Posts: 2.1 Pay Level-8 (₹ 47600 to 151100)"

    jso = _post(t.posts, "Junior Statistical Officer")
    assert (jso.department, jso.pay_level, jso.age_text, jso.provenance.page) == \
        ("Ministry of Statistics & Programme Implementation.", "Level-6", "18-32 years", 3)
    assert jso.provenance.excerpt == "7 | Junior Statistical Officer | Ministry of Statistics & Programme Implementation. | Group “B” | 18-32 years"

    # Rows 10-11 of para 2.2 sit under the CBIC cell of row 9, merged across the page break (p.2 -> p.3)
    examiner = _post(t.posts, "Inspector (Examiner)")
    assert (examiner.department, examiner.classification, examiner.age_text, examiner.pay_level, examiner.provenance.page) == \
        ("Central Board of Indirect Taxes and Customs (CBIC)", "Group “B”", "18-30 years", "Level-7", 3)
    assert examiner.inherited == ["Ministry/Department/Office/ Cadre", "Classification of Post", "Age Limit"]
    assert examiner.provenance.excerpt.endswith("[merged cell(s) continued from the row above: Ministry/Department/Office/ Cadre, "
                                                "Classification of Post, Age Limit]")

    # Same name, different department / pay level: different posts
    assert _post(t.posts, "Auditor", "Offices under CGDA").age_max == 27
    assert _post(t.posts, "Assistant Section Officer", "Central Secretariat Service").age_min == 20
    aso6 = _post(t.posts, "Assistant / Assistant Section Officer", level="Level-6")
    aso7 = _post(t.posts, "Assistant / Assistant Section Officer", level="Level-7")
    assert aso6.post_key != aso7.post_key
    tax = _post(t.posts, "Tax Assistant", "Central Board of Indirect Taxes and Customs (CBIC)")
    assert (tax.pay_level, tax.classification, tax.provenance.page) == ("Level-4", "Group “C”", 4)

    # Para 8: essential qualifications by post heading, "All other Posts" for the rest
    assert jso.education_text.startswith("Bachelor‟s Degree in any subject from a recognized University or Institute with at least 60% "
                                          "Marks in Mathematics at 12th standard level; Or Bachelor‟s Degree in any subject with Statistics")
    assert (jso.education_provenance.page, jso.education_provenance.section) == (16, "Essential Educational Qualifications 8.2")
    state = _post(t.posts, "Assistant Audit Officer (State Cadre)")
    assert "Proficiency in the Regional/official language of the State" in state.education_text
    assert "Desirable" not in state.education_text and state.education_provenance.page == 15
    assert tax.education_text == "Bachelor‟s Degree from a recognized University or equivalent."
    assert tax.education_provenance.section == "Essential Educational Qualifications 8.4"
    assert t.fields["age_crucial_date"].value == date(2026, 8, 1) and t.fields["age_crucial_date"].provenance.page == 6

def test_cgl_age_classes_and_relaxation_table_as_stated():
    t = _tables(CGL)
    assert [(c.label, c.earliest.isoformat(), c.latest.isoformat(), c.provenance.page) for c in t.age_classes] == [
        ("18 27", "1999-08-02", "2008-08-01", 6), ("20 30", "1996-08-02", "2006-08-01", 6),
        # row (iii) is split by the page break; its "than 01-08-2008." is on page 7
        ("18 30", "1996-08-02", "2008-08-01", 6), ("18 32", "1994-08-02", "2008-08-01", 7)]
    assert [(r.code, r.category, r.relaxation, r.provenance.page) for r in t.relaxation_rows][:5] == [
        ("01", "SC/ST", "5 years", 7), ("02", "OBC", "3 years", 7), ("03", "PwBD", "10 years", 7),
        ("04", "PwBD (OBC)", "13 years", 7), ("05", "PwBD (SC/ST)", "15 years", 7)]
    reqs = [(r.req_type, r.value, r.provenance.page) for r in t.relaxations.requirements]
    assert ("AGE_RELAXATION", {"category": "PwBD", "years": 10, "stated": {"UR/EWS": 10, "OBC": 13, "SC/ST": 15}}, 7) in reqs
    assert ("AGE_RELAXATION", {"category": "OBC", "years": 3}, 7) in reqs and ("AGE_RELAXATION", {"category": "SC", "years": 5}, 7) in reqs
    assert ("AGE_RELAXATION_RULE", {"basis": "EX_SERVICEMEN_SERVICE_DEDUCTED", "years": 3}, 7) in reqs
    # Rows 10-13 sit under "Additional permissible relaxation in upper age limit for Group 'C' posts"
    assert ("AGE_RELAXATION_RULE", {"basis": "CENTRAL_GOVT_CIVILIAN_EMPLOYEE", "max_age": 40, "min_service_years": 3,
                                    "categories": ["UR", "EWS", "OBC"], "post_group": "C"}, 7) in reqs
    assert ("AGE_RELAXATION_RULE", {"basis": "WIDOW_DIVORCED_SEPARATED", "max_age": 35, "categories": ["UR", "EWS", "OBC"],
                                    "post_group": "C"}, 8) in reqs
    widow = next(r for r in t.relaxation_rows if r.code == "12")
    assert widow.provenance.excerpt == ("12 Widows/ Divorced Women/ Women judicially separated and who are not remarried. Up to 35 years of age "
                                        "[Additional permissible relaxation in upper age limit for Group „C‟ posts]")

def test_je_table_repeats_header_splits_cells_across_pages_and_merges_organisations():
    t = _tables(JE)
    assert t.warnings == [] and len(t.posts) == 21
    bro = [p for p in t.posts if p.department == "Border Roads Organization"]
    assert [(p.post_name, p.age_text, p.provenance.page) for p in bro] == [("JE(C)", "Up to 30 years", 2), ("JE (E & M)", "Up to 30 years", 2)]
    assert bro[1].inherited == ["S. No.", "Organization", "Age limit"]
    cpwd = _post(t.posts, "JE (C)", "Central Public Works Department (CPWD)")
    assert (cpwd.age_min, cpwd.age_max, cpwd.education_text) == (None, 32, "Diploma in Civil Engineering from a recognized University or Institute.")
    # The DGQA JE(E) qualification ends on page 3 ("field") under the repeated header
    dgqa = _post(t.posts, "JE(E)", "DGQA-NAVAL, Ministry of Defence")
    assert dgqa.education_text.endswith("(b) Two-Year experience in the respective field") and dgqa.provenance.page == 2
    sa = _post(t.posts, "Scientific Assistant")
    assert sa.department == "India Meteorological Department" and sa.provenance.page == 3
    assert sa.education_text.endswith("Telecommunication Engineering or Communication Engineering from a recognised institution or university")
    # Page 1: "The posts are of Group 'B' in Level-6 (Rs. 35400-112400/-) of the Pay Matrix"
    assert {(p.pay_level, p.classification) for p in t.posts} == {("Level-6", "Group ‘B’")}
    assert t.fields["pay_level_all_posts"].provenance.page == 1
    assert [(c.label, c.earliest.isoformat(), c.latest.isoformat()) for c in t.age_classes] == [
        ("up to 30", "1996-08-02", "2008-08-01"), ("up to 32", "1994-08-02", "2008-08-01")]

def test_parse_exam_pdf_gives_each_post_its_own_age_and_education_rows():
    body = (SSC / JE).read_bytes()
    parsed = parse_exam_pdf(body, extract_pages(body, ocr=False))
    assert parsed.parser_version == "ssc_exam_notice/2+ssc_tables/1" and parsed.warnings == []
    cpwd = next(p for p in parsed.posts if p.post_name == "JE (C)" and p.department == "Central Public Works Department (CPWD)")
    ages = {r.value["category"]: r for r in cpwd.requirements if r.req_type == "AGE_MAX"}
    # "Up to 32 years" + "born not earlier than 02.08.1994 and not later than 01.08.2008" on 01.08.2026
    assert ages["UR"].value == {"category": "UR", "min_age": 18, "max_age": 32, "born_not_earlier_than": "1994-08-02",
                                "born_not_later_than": "2008-08-01"}
    assert ages["SC"].value["max_age"] == 37 and ages["SC"].value["born_not_earlier_than"] == "1989-08-02"
    assert ages["OBC"].provenance.excerpt.endswith("p.8: 02 OBC 3 years")
    assert "p.8: For the posts for which the age limit is up to 32 years Candidate must have been born not earlier than" \
        in ages["OBC"].provenance.excerpt
    assert [r.req_type for r in parsed.requirements].count("EDUCATION") == 0  # post-wise only
    assert parsed.value("age_crucial_date") == datetime(2026, 8, 1, tzinfo=IST)

async def _ingest(db_session, name):
    source = await get_or_create_source(db_session, ssc.SSC_SOURCE)
    result = await ingest_exam_pdf(db_session, source, SOURCES[name]["url"], (SSC / name).read_bytes())
    await db_session.commit()
    return result

@pytest.mark.asyncio
async def test_cgl_ingestion_stores_posts_and_post_requirements_idempotently(db_session):
    first = await _ingest(db_session, CGL)
    assert first.created and first.warnings == []
    exam = await db_session.get(Exam, first.exam_id)
    assert (exam.name, exam.exam_code, exam.external_ref) == ("Combined Graduate Level Examination, 2026", "CGL", "HQ-C11018/1/2026-C-1")
    assert exam.age_crucial_date == datetime(2026, 8, 1, tzinfo=IST) and exam.confidence == 1.0
    posts = {(p.post_name, p.department, p.pay_level): p for p in (await db_session.execute(select(ExamPost))).scalars()}
    assert len(posts) == 40
    jso = posts[("Junior Statistical Officer", "Ministry of Statistics & Programme Implementation.", "Level-6")]
    assert (jso.age_min, jso.age_max, jso.classification, jso.source_page) == (18, 32, "Group “B”", 3)
    rows = (await db_session.execute(select(ExamRequirement).where(ExamRequirement.post_id == jso.id))).scalars().all()
    assert sorted(r.req_type for r in rows) == ["AGE_MAX"] * 5 + ["EDUCATION"]
    ur = next(json.loads(r.value) for r in rows if r.req_type == "AGE_MAX" and '"UR"' in r.value)
    assert ur["born_not_earlier_than"] == "1994-08-02" and ur["max_age"] == 32
    exam_level = (await db_session.execute(select(ExamRequirement.req_type).where(
        ExamRequirement.exam_id == exam.id, ExamRequirement.post_id.is_(None)))).scalars().all()
    # The text parser's exam-level paragraphs (incl. a PwBD certificate paragraph on p.19) gave way to the post-wise ones
    assert "EDUCATION" not in exam_level and "AGE_MAX" not in exam_level and "CITIZENSHIP" in exam_level
    assert exam.education_text.splitlines()[0] == "Bachelor‟s Degree from a recognized University or Institute."

    count = (await db_session.execute(select(func.count(ExamRequirement.id)))).scalar_one()
    again = await _ingest(db_session, CGL)
    assert again.exam_id == first.exam_id and again.skipped_reason == "already parsed with this parser version"
    assert (await db_session.execute(select(func.count(ExamRequirement.id)))).scalar_one() == count

    # A newer parser re-parses in place: same posts, old requirement rows superseded (kept), the same number active
    exam.parser_version = "ssc_exam_notice/0"
    await db_session.commit()
    reparsed = await _ingest(db_session, CGL)
    assert reparsed.reparsed and reparsed.exam_id == first.exam_id
    assert (await db_session.execute(select(func.count(ExamPost.id)))).scalar_one() == 40
    active = (await db_session.execute(select(func.count(ExamRequirement.id)).where(ExamRequirement.superseded_at.is_(None)))).scalar_one()
    assert active == count and (await db_session.execute(select(func.count(ExamRequirement.id)))).scalar_one() == 2 * count
