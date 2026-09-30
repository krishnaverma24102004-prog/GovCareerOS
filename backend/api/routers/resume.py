"""
Truth-locked resumes. Every line is a claim linked to a profile Fact and its Evidence; see backend/resume/builder.py.
All lookups are scoped to the authenticated user; other users' resumes are reported as 404.
"""
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from govcareeros.backend.api.deps import current_user, get_session
from govcareeros.backend.eligibility.engine import EligibilityInputError
from govcareeros.backend.models.artifact import RawDocument
from govcareeros.backend.models.fact import Evidence, Fact
from govcareeros.backend.models.resume import Resume, ResumeClaim
from govcareeros.backend.models.user import User
from govcareeros.backend.models.vacancy import Vacancy
from govcareeros.backend.resume import render
from govcareeros.backend.resume.builder import ResumeInputError, build_resume

router = APIRouter(prefix="/resume", tags=["resume"])

class ResumeIn(BaseModel):
    profile_version_id: uuid.UUID
    vacancy_id: Optional[uuid.UUID] = None

class EvidenceOut(BaseModel):
    id: uuid.UUID
    evidence_type: str
    source: Optional[str]
    artifact_id: Optional[uuid.UUID]
    artifact_url: Optional[str]
    artifact_content_hash: Optional[str]

class FactOut(BaseModel):
    id: uuid.UUID
    subject: str
    predicate: str
    value: str
    status: str
    evidence: List[EvidenceOut]

class ClaimOut(BaseModel):
    id: uuid.UUID
    section: str
    position: int
    item_ref: str
    field: str
    text: str
    verified: bool
    requirement_id: Optional[uuid.UUID]
    requirement_quote: Optional[str]
    requirement_page: Optional[int]
    requirement_outcome: Optional[str]
    fact: FactOut

class ResumeOut(BaseModel):
    id: uuid.UUID
    profile_version_id: uuid.UUID
    vacancy_id: Optional[uuid.UUID]
    eligibility_result_id: Optional[uuid.UUID]
    builder_version: str
    created_at: datetime
    claims: List[ClaimOut]

async def _owned(session: AsyncSession, user: User, resume_id: uuid.UUID) -> Resume:
    resume = (await session.execute(
        select(Resume).options(selectinload(Resume.claims)).where(Resume.id == resume_id, Resume.user_id == user.id)
    )).scalar_one_or_none()
    if resume is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "resume not found")
    return resume

async def _out(session: AsyncSession, resume: Resume) -> ResumeOut:
    fact_ids = {c.fact_id for c in resume.claims}
    facts = {f.id: f for f in (await session.execute(select(Fact).where(Fact.id.in_(fact_ids)))).scalars()}
    rows = (await session.execute(
        select(Evidence, RawDocument).outerjoin(RawDocument, Evidence.artifact_id == RawDocument.id)
        .where(Evidence.fact_id.in_(fact_ids)).order_by(Evidence.created_at, Evidence.id))).all()
    evidence = {}
    for e, doc in rows:
        evidence.setdefault(e.fact_id, []).append(EvidenceOut(
            id=e.id, evidence_type=e.evidence_type, source=e.source, artifact_id=e.artifact_id,
            artifact_url=doc.url if doc else None, artifact_content_hash=doc.content_hash if doc else None))
    claims = []
    for c in sorted(resume.claims, key=lambda c: c.position):
        f = facts[c.fact_id]
        claims.append(ClaimOut(
            id=c.id, section=c.section, position=c.position, item_ref=c.item_ref, field=c.field, text=c.text, verified=c.verified,
            requirement_id=c.requirement_id, requirement_quote=c.requirement_quote, requirement_page=c.requirement_page,
            requirement_outcome=c.requirement_outcome,
            fact=FactOut(id=f.id, subject=f.subject, predicate=f.predicate, value=f.value, status=f.status, evidence=evidence.get(f.id, [])),
        ))
    return ResumeOut(id=resume.id, profile_version_id=resume.profile_version_id, vacancy_id=resume.vacancy_id,
                     eligibility_result_id=resume.eligibility_result_id, builder_version=resume.builder_version,
                     created_at=resume.created_at, claims=claims)

@router.post("", response_model=ResumeOut, status_code=status.HTTP_201_CREATED)
async def create_resume(body: ResumeIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    """Builds a resume from one explicit profile version, optionally tailored to a vacancy."""
    try:
        resume = await build_resume(session, user.id, body.profile_version_id, body.vacancy_id)
    except (ResumeInputError, EligibilityInputError) as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
    resume = await _owned(session, user, resume.id)
    vacancy = await session.get(Vacancy, resume.vacancy_id) if resume.vacancy_id else None
    render.write_files(resume, resume.claims, vacancy)
    await session.flush()
    await session.refresh(resume, ["created_at"])
    return await _out(session, resume)

@router.get("/{resume_id}", response_model=ResumeOut)
async def get_resume(resume_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    return await _out(session, await _owned(session, user, resume_id))

@router.get("/{resume_id}/download")
async def download(resume_id: uuid.UUID, format: Literal["docx", "html"] = Query("docx"),
                   user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    resume = await _owned(session, user, resume_id)
    path = resume.docx_path if format == "docx" else resume.html_path
    if not path or not Path(path).is_file():
        # Files are derived from the stored claims, so a missing file is simply rendered again
        vacancy = await session.get(Vacancy, resume.vacancy_id) if resume.vacancy_id else None
        render.write_files(resume, resume.claims, vacancy)
        path = resume.docx_path if format == "docx" else resume.html_path
    media = ("application/vnd.openxmlformats-officedocument.wordprocessingml.document" if format == "docx"
             else "text/html; charset=utf-8")
    return FileResponse(path, media_type=media, filename=f"resume-{resume.id}.{format}")
