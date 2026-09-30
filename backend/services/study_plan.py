"""
Deterministic study plan generator (no LLM).

Rules:
- Weeks = whole weeks between the start date and the exam date; at least 2 are needed.
- The last 15% of weeks (rounded up, at least one) are for revision and previous-year-paper practice; the rest
  are study weeks.
- Study hours (study weeks x weekly hours) are shared among syllabus topics in proportion to their size, the
  number of official subtopics. Topics are taken in syllabus order and fill the weeks back to back; a topic that
  does not fit in the rest of a week continues in the next one (one plan item per topic per week).
- Revision weeks: one REVISION item per paper (section) and one PYQ item per downloaded question paper, both
  dealt round-robin over the revision weeks (papers newest year first). A week with both kinds gives half its
  hours to each; a week with nothing assigned gets one full-syllabus revision item.
- When the user picks sections, only those papers' topics are planned, and only question papers whose name
  matches a picked section ("General Economics Paper - I" ~ "General Economics-I") are used.
"""
import math
import re
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Sequence

GENERATOR_VERSION = "study_plan/1"
REVISION_SHARE = 0.15
MIN_WEEKS = 2

class PlanError(ValueError):
    """The request cannot produce a plan (too few weeks, nothing to study)."""

@dataclass
class TopicInput:
    section: str
    topic: str
    size: int  # number of subtopics in the official syllabus
    page: Optional[int] = None

@dataclass
class PaperInput:
    id: object
    year: int
    paper_name: str

@dataclass
class PlannedItem:
    position: int
    week: int
    kind: str  # TOPIC, REVISION, PYQ
    section: Optional[str]
    topic: str
    hours: float
    page: Optional[int] = None
    question_paper_id: Optional[object] = None

@dataclass
class Plan:
    total_weeks: int
    revision_weeks: int
    items: List[PlannedItem]

def section_key(name: str) -> str:
    """Comparable form of a paper name: "General Economics Paper - I" and "General Economics-I" -> "generaleconomicsi"."""
    name = re.sub(r"\([^)]*\)", " ", name.lower())
    name = re.sub(r"\bpaper\b", " ", name)
    return re.sub(r"[^a-z0-9]", "", name)

def week_counts(start: date, exam_date: date) -> tuple:
    total = (exam_date - start).days // 7
    if total < MIN_WEEKS:
        raise PlanError(f"need at least {MIN_WEEKS} whole weeks before the exam, have {max(total, 0)}")
    revision = max(1, math.ceil(total * REVISION_SHARE))
    return total, revision

def build_plan(start: date, exam_date: date, weekly_hours: float, topics: Sequence[TopicInput],
               papers: Sequence[PaperInput] = ()) -> Plan:
    if weekly_hours <= 0:
        raise PlanError("weekly_hours must be positive")
    topics = [t for t in topics if t.size > 0]
    if not topics:
        raise PlanError("no syllabus topics to plan")
    total, revision = week_counts(start, exam_date)
    study_weeks = total - revision
    items: List[PlannedItem] = []

    def add(week, kind, section, topic, hours, page=None, paper_id=None):
        items.append(PlannedItem(len(items) + 1, week, kind, section, topic, round(hours, 2), page, paper_id))

    total_size = sum(t.size for t in topics)
    study_hours = study_weeks * weekly_hours
    cursor = 0.0
    eps = 1e-9
    for t in topics:
        remaining = study_hours * t.size / total_size
        while remaining > eps:
            week = min(int((cursor + eps) // weekly_hours) + 1, study_weeks)
            take = remaining if week == study_weeks else min(remaining, week * weekly_hours - cursor)
            if take >= 0.005:  # floating-point crumbs at a week boundary are not worth an item
                add(week, "TOPIC", t.section, t.topic, take, t.page)
            cursor += take
            remaining -= take

    sections: List[str] = list(dict.fromkeys(t.section for t in topics))
    first_page: Dict[str, Optional[int]] = {}
    for t in topics:
        first_page.setdefault(t.section, t.page)
    section_of = {section_key(s): s for s in sections}
    ordered_papers = sorted(papers, key=lambda p: (-p.year, p.paper_name))
    for offset in range(revision):
        week = study_weeks + offset + 1
        week_sections = sections[offset::revision]
        week_papers = ordered_papers[offset::revision]
        if not week_sections and not week_papers:
            add(week, "REVISION", None, "Full syllabus revision", weekly_hours)
            continue
        share = weekly_hours / 2 if week_sections and week_papers else weekly_hours
        for s in week_sections:
            add(week, "REVISION", s, f"Revise {s}", share / len(week_sections), first_page[s])
        for p in week_papers:
            add(week, "PYQ", section_of.get(section_key(p.paper_name)), f"Solve {p.paper_name} ({p.year})", share / len(week_papers), None, p.id)
    return Plan(total, revision, items)

def match_papers(papers: Sequence[PaperInput], sections: Optional[Sequence[str]]) -> List[PaperInput]:
    if not sections:
        return list(papers)
    wanted = {section_key(s) for s in sections}
    return [p for p in papers if section_key(p.paper_name) in wanted]
