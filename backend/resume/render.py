"""
Renders a stored resume to HTML and DOCX from its claims only, so a file can always be regenerated and never
contains text that is not a claim (apart from section titles, field labels and the target vacancy caption).
Unverified claims carry a visible "[unverified]" mark in both formats.
"""
import html
import io
from itertools import groupby
from pathlib import Path
from typing import List, Optional, Sequence

from docx import Document
from docx.shared import Pt

from govcareeros.backend.config.settings import settings
from govcareeros.backend.models.resume import Resume, ResumeClaim
from govcareeros.backend.models.vacancy import Vacancy
from govcareeros.backend.resume.builder import MEETS

TITLES = {
    "personal_info": "Personal details", MEETS: "Meets requirement", "experience": "Experience", "education": "Education",
    "projects": "Projects", "skills": "Skills", "certifications": "Certifications", "languages": "Languages",
    "achievements": "Achievements",
}
UNVERIFIED = "[unverified]"
NOTE = f"Lines marked {UNVERIFIED} are stated by the candidate and not yet backed by a document."

def _label(field: str) -> str:
    return field.replace("_", " ").capitalize()

def _caption(vacancy: Optional[Vacancy]) -> Optional[str]:
    if vacancy is None:
        return None
    advt = f", Advt {vacancy.advertisement_number}" if vacancy.advertisement_number else ""
    return f"Prepared for: {vacancy.post_name}, {vacancy.organization}{advt}"

def _sections(claims: Sequence[ResumeClaim]):
    ordered = sorted(claims, key=lambda c: c.position)
    for section, in_section in groupby(ordered, key=lambda c: c.section):
        yield section, [list(item) for _, item in groupby(in_section, key=lambda c: c.requirement_id if c.section == MEETS else c.item_ref)]

def _requirement_line(c: ResumeClaim) -> str:
    page = f" (notification page {c.requirement_page})" if c.requirement_page else ""
    return f"Notification requires: “{c.requirement_quote}”{page}"

def render_html(claims: Sequence[ResumeClaim], vacancy: Optional[Vacancy] = None) -> str:
    def mark(c):
        return "" if c.verified else f' <span class="unverified">{UNVERIFIED}</span>'
    out = ["<!doctype html><html><head><meta charset=\"utf-8\"><title>Resume</title><style>",
           "body{font-family:Georgia,serif;max-width:46rem;margin:2rem auto;padding:0 1rem;color:#222}"
           ".unverified{color:#b00020;font-size:.85em;font-style:italic}blockquote{margin:.3rem 0 .3rem 1rem;color:#444}"
           "h2{border-bottom:1px solid #ccc;font-size:1.1rem}.item{margin:.6rem 0}.note{color:#666;font-size:.85em}",
           "</style></head><body>"]
    caption = _caption(vacancy)
    if caption:
        out.append(f'<p class="note">{html.escape(caption)}</p>')
    for section, items in _sections(claims):
        out.append(f"<h2>{html.escape(TITLES.get(section, section))}</h2>")
        for item in items:
            out.append('<div class="item">')
            if section == MEETS:
                out.append(f"<blockquote>{html.escape(_requirement_line(item[0]))}</blockquote>")
                for c in item:
                    outcome = " (conditional)" if c.requirement_outcome == "CONDITIONAL" else ""
                    out.append(f"<div>Profile: {html.escape(c.text)}{outcome}{mark(c)}</div>")
            else:
                head, rest = item[0], item[1:]
                out.append(f"<div><strong>{html.escape(head.text)}</strong>{mark(head)}</div>")
                for c in rest:
                    out.append(f"<div>{html.escape(_label(c.field))}: {html.escape(c.text)}{mark(c)}</div>")
            out.append("</div>")
    out.append(f'<p class="note">{html.escape(NOTE)}</p></body></html>')
    return "\n".join(out)

def render_docx(claims: Sequence[ResumeClaim], vacancy: Optional[Vacancy] = None) -> bytes:
    doc = Document()
    def line(parts: List[tuple], style: Optional[str] = None):
        p = doc.add_paragraph(style=style)
        for text, bold, italic in parts:
            run = p.add_run(text)
            run.bold, run.italic = bold, italic
        return p
    def mark(c):
        return [] if c.verified else [(" " + UNVERIFIED, False, True)]
    caption = _caption(vacancy)
    if caption:
        line([(caption, False, True)])
    for section, items in _sections(claims):
        doc.add_heading(TITLES.get(section, section), level=2)
        for item in items:
            if section == MEETS:
                line([(_requirement_line(item[0]), False, True)])
                for c in item:
                    outcome = " (conditional)" if c.requirement_outcome == "CONDITIONAL" else ""
                    line([("Profile: " + c.text + outcome, False, False)] + mark(c))
            else:
                line([(item[0].text, True, False)] + mark(item[0]))
                for c in item[1:]:
                    line([(f"{_label(c.field)}: {c.text}", False, False)] + mark(c))
    line([(NOTE, False, True)]).runs[0].font.size = Pt(8)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()

def resume_dir(resume: Resume) -> Path:
    return settings.storage_dir / "resumes" / str(resume.user_id)

def write_files(resume: Resume, claims: Sequence[ResumeClaim], vacancy: Optional[Vacancy]) -> None:
    folder = resume_dir(resume)
    folder.mkdir(parents=True, exist_ok=True)
    html_path, docx_path = folder / f"{resume.id}.html", folder / f"{resume.id}.docx"
    html_path.write_text(render_html(claims, vacancy), encoding="utf-8")
    docx_path.write_bytes(render_docx(claims, vacancy))
    resume.html_path, resume.docx_path = str(html_path), str(docx_path)
