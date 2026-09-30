"""
Truth-locked resume builder.

Rules:
- Every resume line is a ResumeClaim holding ONE profile field value verbatim (dates as ISO dates, numbers without
  trailing ".0"; nothing else is rewritten). Nothing is generated: no metrics, designations, technologies,
  certifications, responsibilities or government experience that are not in the profile row.
- Every claim links to a Fact of the same profile version, matched on (subject = profile section, predicate = column,
  value). A value without a Fact gets one created (UNVERIFIED) with USER_ATTESTED evidence naming the profile row.
  A value whose only matching Facts are REJECTED is left out of the resume.
- A claim is verified only when its Fact is VERIFIED (reviewer decision); attached evidence is shown but does not verify;
  every other claim is rendered with a visible "unverified" mark.
- Tailoring to a vacancy is deterministic: items and sections are ordered by how many relevance terms
  (relevance.scoring.terms) they share with the vacancy's post name, department, duties, desirable and
  education/experience requirement text. The "Meets requirement" section comes from a stored eligibility result:
  each MET/CONDITIONAL education or experience check quotes the notification requirement (with page) next to the
  profile fact the engine matched.
"""
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.eligibility import engine as eligibility_engine
from govcareeros.backend.models.fact import Evidence, Fact
from govcareeros.backend.models.resume import Resume, ResumeClaim
from govcareeros.backend.models.user import (
    Achievement, Certification, Education, Experience, Language, PersonalInfo, ProfileVersion, Project, Skill,
)
from govcareeros.backend.models.vacancy import Vacancy, VacancyRequirement
from govcareeros.backend.relevance.scoring import terms

BUILDER_VERSION = "truth_locked_resume/1"

# Section -> (model, columns quoted in order, sort key for the untailored order). The first column is the item heading.
_SECTIONS: Dict[str, Tuple[type, Tuple[str, ...]]] = {
    "personal_info": (PersonalInfo, ("full_name", "contact", "address", "date_of_birth", "nationality")),
    "experience": (Experience, ("designation", "organization", "employment_type", "start_date", "end_date", "domain",
                                "responsibilities", "skills_used")),
    "education": (Education, ("degree", "specialization", "institution", "university", "start_date", "end_date",
                              "percentage", "cgpa", "subjects")),
    "projects": (Project, ("project_name", "role", "domain", "start_date", "end_date", "description", "technologies")),
    "skills": (Skill, ("skill_name", "proficiency")),
    "certifications": (Certification, ("name", "issuer", "issue_date", "expiry_date", "credential_id")),
    "languages": (Language, ("language", "proficiency")),
    "achievements": (Achievement, ("title", "date", "description")),
}
MEETS = "meets_requirement"

class ResumeInputError(ValueError):
    pass

def as_text(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    return text or None

def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()

def _recency(row) -> tuple:
    # Ongoing roles first, then reverse-chronological, undated last; ties keep the order the candidate entered them
    ongoing = getattr(row, "start_date", None) is not None and hasattr(row, "end_date") and row.end_date is None
    d =getattr(row, "end_date", None) or getattr(row, "start_date", None) or getattr(row, "date", None) \
        or getattr(row, "issue_date", None)
    return (not ongoing, d is None, -(d.toordinal() if d else 0), row.created_at.timestamp() if row.created_at else 0, str(row.id))

@dataclass
class _Line:
    section: str
    item_ref: str
    field: str
    text: str
    fact: Fact
    requirement: Optional[VacancyRequirement] = None
    outcome: Optional[str] = None

class _Facts:
    """The version's Facts indexed by (subject, predicate, value); creates missing ones."""

    def __init__(self, session: AsyncSession, version_id: uuid.UUID, facts: Sequence[Fact], evidence: Sequence[Evidence]):
        self.session, self.version_id = session, version_id
        self.by_key: Dict[tuple, List[Fact]] = {}
        for f in facts:
            self.by_key.setdefault((f.subject, f.predicate, _norm(f.value)), []).append(f)
        self.evidence: Dict[uuid.UUID, List[Evidence]] = {}
        for e in evidence:
            self.evidence.setdefault(e.fact_id, []).append(e)

    async def for_value(self, section: str, row, field: str, text: str) -> Optional[Fact]:
        found = self.by_key.get((section, field, _norm(text)), [])
        usable = [f for f in found if f.status != "REJECTED"]
        if usable:
            return max(usable, key=lambda f: (f.status == "VERIFIED", self.verified(f)))
        if found:
            return None  # the candidate's claim was rejected: never print it
        fact = Fact(id=uuid.uuid4(), profile_version_id=self.version_id, subject=section, predicate=field, value=text, status="UNVERIFIED")
        ev = Evidence(fact_id=fact.id, evidence_type="USER_ATTESTED", source=f"profile row {row.__tablename__}/{row.id}.{field}")
        self.session.add_all([fact, ev])
        self.by_key[(section, field, _norm(text))] = [fact]
        self.evidence[fact.id] = [ev]
        return fact

    def verified(self, fact: Fact) -> bool:
        # Only a reviewer-verified fact counts. Evidence the candidate attaches (even a stored document) is shown
        # in the claim chain but does not verify it: the RawDocument may be a public notice, not their certificate.
        return fact.status == "VERIFIED"

def vacancy_terms(vacancy: Vacancy, reqs: Sequence[VacancyRequirement]) -> set:
    text = [vacancy.post_name, vacancy.department or "", vacancy.ministry or ""]
    text += [r.value for r in reqs if r.req_type in ("DUTIES", "DESIRABLE", "EDUCATION", "EXPERIENCE")]
    return terms(" ".join(text))

async def build_resume(session: AsyncSession, user_id: uuid.UUID, profile_version_id: uuid.UUID,
                       vacancy_id: Optional[uuid.UUID] = None) -> Resume:
    version = await session.get(ProfileVersion, profile_version_id)
    if version is None or version.user_id != user_id:
        raise ResumeInputError("profile version not found")
    vacancy = await session.get(Vacancy, vacancy_id) if vacancy_id else None
    if vacancy_id and vacancy is None:
        raise ResumeInputError("vacancy not found")

    facts = (await session.execute(select(Fact).where(Fact.profile_version_id == profile_version_id))).scalars().all()
    evidence = (await session.execute(select(Evidence).where(Evidence.fact_id.in_([f.id for f in facts])))).scalars().all() if facts else []
    index = _Facts(session, profile_version_id, facts, evidence)

    reqs: List[VacancyRequirement] = []
    if vacancy:
        reqs = list((await session.execute(select(VacancyRequirement).where(
            VacancyRequirement.vacancy_id == vacancy.id, VacancyRequirement.superseded_at.is_(None)))).scalars())
    wanted = vacancy_terms(vacancy, reqs) if vacancy else set()

    # section -> [(relevance, recency, row, [lines])]
    items: Dict[str, list] = {}
    rows_by_section: Dict[str, list] = {}
    for section, (model, columns) in _SECTIONS.items():
        rows = (await session.execute(select(model).where(model.profile_version_id == profile_version_id))).scalars().all()
        rows_by_section[section] = rows
        for row in rows:
            lines = []
            for field in columns:
                text = as_text(getattr(row, field))
                if text is None:
                    continue
                fact = await index.for_value(section, row, field, text)
                if fact is not None:
                    lines.append(_Line(section, f"{section}:{row.id}", field, text, fact))
            if lines:
                score = len(terms(" ".join(l.text for l in lines)) & wanted)
                items.setdefault(section, []).append((score, _recency(row), row, lines))

    order = list(_SECTIONS)
    for section in items:
        # personal details stay as entered; everything else relevance first (tailored), then most recent
        if section != "personal_info":
            items[section].sort(key=lambda it: (-it[0], it[1]))
    if vacancy:
        rest = sorted([s for s in order if s != "personal_info"],
                      key=lambda s: (-max((it[0] for it in items.get(s, [])), default=0), order.index(s)))
        order = ["personal_info", MEETS] + rest

    resume = Resume(id=uuid.uuid4(), user_id=user_id, profile_version_id=profile_version_id,
                    vacancy_id=vacancy.id if vacancy else None, builder_version=BUILDER_VERSION)
    meets: List[_Line] = []
    if vacancy:
        result = await eligibility_engine.evaluate(session, user_id, profile_version_id, vacancy.id)
        resume.eligibility_result_id = result.id
        meets = await _meets_lines(result, {r.id: r for r in reqs}, rows_by_section, index)
    session.add(resume)
    await session.flush()

    position = 0
    for section in order:
        lines = meets if section == MEETS else [l for it in items.get(section, []) for l in it[3]]
        for line in lines:
            session.add(ResumeClaim(
                resume_id=resume.id, fact_id=line.fact.id, section=line.section, position=position, item_ref=line.item_ref,
                field=line.field, text=line.text, verified=index.verified(line.fact),
                requirement_id=line.requirement.id if line.requirement else None,
                requirement_quote=line.requirement.value if line.requirement else None,
                requirement_page=line.requirement.source_page if line.requirement else None,
                requirement_outcome=line.outcome,
            ))
            position += 1
    await session.flush()
    return resume

async def _meets_lines(result, reqs: Dict[uuid.UUID, VacancyRequirement], rows: Dict[str, list], index: _Facts) -> List[_Line]:
    """Pairs each met education/experience requirement with the profile row the engine names in its evidence."""
    out = []
    for ev in sorted(result.evidence, key=lambda e: (e.check_name, e.requirement)):
        req = reqs.get(ev.requirement_id)
        if req is None or ev.check_name not in ("EDUCATION", "EXPERIENCE") or ev.outcome not in ("MET", "CONDITIONAL"):
            continue
        if ev.check_name == "EDUCATION":
            # rules.check_education writes the matched entry's degree first; the longest prefix wins ("B.A. LL.B." over "B.A.")
            matched = sorted([("education", r, "degree") for r in rows["education"] if ev.profile_evidence.startswith(r.degree)],
                             key=lambda m: -len(m[1].degree))[:1]
        else:
            # rules.check_experience names each counted entry as "designation @ organization"
            matched = [("experience", r, "designation") for r in rows["experience"]
                       if f"{r.designation} @ {r.organization}" in ev.profile_evidence]
        for section, row, field in matched:
            text = as_text(getattr(row, field))
            fact = await index.for_value(section, row, field, text)
            if fact is not None:
                out.append(_Line(MEETS, f"{section}:{row.id}", field, text, fact, req, ev.outcome))
    return out
