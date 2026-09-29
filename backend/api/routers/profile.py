"""
Master profile API. Every PUT/PATCH creates a new immutable ProfileVersion; nothing is edited in place.
All lookups are scoped to the authenticated user; other users' records are reported as 404.
"""
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.api.deps import current_user, get_session
from govcareeros.backend.models.artifact import RawDocument
from govcareeros.backend.models.fact import Evidence, Fact
from govcareeros.backend.models.user import ProfileVersion, User
from govcareeros.backend.repositories.fact_repository import FactRepository
from govcareeros.backend.repositories.profile_repository import ProfileRepository
from govcareeros.backend.schemas.profile import (
    EvidenceIn, EvidenceOut, FactIn, FactOut, ProfileIn, ProfileOut, ProfileVersionSummary,
)
from govcareeros.backend.services.profile_service import ProfileService

router = APIRouter(prefix="/profile", tags=["profile"])

def _not_found(what: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"{what} not found")

async def _profile_out(repo: ProfileRepository, version: ProfileVersion) -> ProfileOut:
    sections = await repo.load_sections(version.id)
    return ProfileOut(version=ProfileVersionSummary.model_validate(version), **sections)

@router.get("", response_model=ProfileOut)
async def get_profile(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    repo = ProfileRepository(session)
    latest = await repo.latest_version(user.id)
    if latest is None:
        raise _not_found("profile")
    return await _profile_out(repo, latest)

async def _save(body: ProfileIn, user: User, session: AsyncSession, carry_over: bool) -> ProfileOut:
    sections = body.model_dump(exclude={"change_summary"}, exclude_none=False)
    # Distinguish "section not sent" (None) from "section sent as empty list"
    sections = {k: v for k, v in sections.items() if k in body.model_fields_set}
    version = await ProfileService(session).save_profile(user.id, sections, body.change_summary, carry_over)
    return await _profile_out(ProfileRepository(session), version)

@router.put("", response_model=ProfileOut, status_code=status.HTTP_201_CREATED)
async def replace_profile(body: ProfileIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    return await _save(body, user, session, carry_over=False)

@router.patch("", response_model=ProfileOut, status_code=status.HTTP_201_CREATED)
async def update_profile(body: ProfileIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    return await _save(body, user, session, carry_over=True)

@router.get("/versions", response_model=List[ProfileVersionSummary])
async def list_versions(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    return await ProfileRepository(session).list_versions(user.id)

@router.get("/versions/{version_id}", response_model=ProfileOut)
async def get_version(version_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    repo = ProfileRepository(session)
    version = await repo.get_owned_version(user.id, version_id)
    if version is None:
        raise _not_found("profile version")
    return await _profile_out(repo, version)

async def _owned_fact(session: AsyncSession, user: User, fact_id: uuid.UUID) -> Fact:
    res = await session.execute(
        select(Fact).join(ProfileVersion, Fact.profile_version_id == ProfileVersion.id)
        .where(Fact.id == fact_id, ProfileVersion.user_id == user.id)
    )
    fact = res.scalar_one_or_none()
    if fact is None:
        raise _not_found("fact")
    return fact

@router.get("/facts", response_model=List[FactOut])
async def list_facts(
    version_id: Optional[uuid.UUID] = Query(None, description="defaults to the latest version"),
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session),
):
    repo = ProfileRepository(session)
    version = await (repo.get_owned_version(user.id, version_id) if version_id else repo.latest_version(user.id))
    if version is None:
        raise _not_found("profile version")
    res = await session.execute(select(Fact).where(Fact.profile_version_id == version.id).order_by(Fact.created_at))
    return res.scalars().all()

@router.post("/facts", response_model=FactOut, status_code=status.HTTP_201_CREATED)
async def create_fact(body: FactIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    latest = await ProfileRepository(session).latest_version(user.id)
    if latest is None:
        raise _not_found("profile")
    fact = await FactRepository(session).create_fact(latest.id, body.subject, body.predicate, body.value)
    await session.refresh(fact)
    return fact

@router.get("/facts/{fact_id}", response_model=FactOut)
async def get_fact(fact_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    return await _owned_fact(session, user, fact_id)

async def _evidence_out(session: AsyncSession, fact_id: uuid.UUID) -> List[EvidenceOut]:
    res = await session.execute(
        select(Evidence, RawDocument).outerjoin(RawDocument, Evidence.artifact_id == RawDocument.id)
        .where(Evidence.fact_id == fact_id).order_by(Evidence.created_at)
    )
    return [
        EvidenceOut.model_validate(e).model_copy(update={
            "artifact_content_hash": doc.content_hash if doc else None,
            "artifact_url": doc.url if doc else None,
        })
        for e, doc in res.all()
    ]

@router.get("/facts/{fact_id}/evidence", response_model=List[EvidenceOut])
async def list_evidence(fact_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    await _owned_fact(session, user, fact_id)
    return await _evidence_out(session, fact_id)

@router.post("/facts/{fact_id}/evidence", response_model=EvidenceOut, status_code=status.HTTP_201_CREATED)
async def add_evidence(fact_id: uuid.UUID, body: EvidenceIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    await _owned_fact(session, user, fact_id)
    if body.raw_document_id and await session.get(RawDocument, body.raw_document_id) is None:
        raise _not_found("raw document")
    evidence = await FactRepository(session).add_evidence(fact_id, body.evidence_type, body.raw_document_id, body.source)
    return next(e for e in await _evidence_out(session, fact_id) if e.id == evidence.id)
