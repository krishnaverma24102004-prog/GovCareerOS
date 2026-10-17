"""
Master profile API. Every PUT/PATCH creates a new immutable ProfileVersion; nothing is edited in place.
Storing eligibility data (category, date of birth, gender, citizenship, disability, certificates, service details) needs
consent (DPDP).
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
from govcareeros.backend.models.user import PersonalInfo, ProfileVersion, ReservationCertificate, User
from govcareeros.backend.privacy import enforcement, notice
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

async def _require_consent_for(session: AsyncSession, user: User, sections: dict, carry_over: bool) -> None:
    """The version about to be written holds category/caste, date of birth, gender, citizenship, disability, certificates
    or service details (sent now, or carried over by PATCH): that needs active ELIGIBILITY_CHECK consent, and guardian consent if the
    date of birth is under 18. Clearing those fields never needs consent, so a person can always remove them."""
    personal, has_certs = sections.get("personal_info"), bool(sections.get("reservation_certificates"))
    latest = await ProfileRepository(session).latest_version(user.id) if carry_over else None
    if latest is not None:
        # A section left out or sent as null is carried over from the latest version (ProfileService.save_profile)
        if sections.get("personal_info") is None:
            row = (await session.execute(select(PersonalInfo).where(PersonalInfo.profile_version_id == latest.id))).scalar_one_or_none()
            personal = {k: getattr(row, k) for k in enforcement.SENSITIVE_PERSONAL_FIELDS} if row else None
        if sections.get("reservation_certificates") is None:
            has_certs = (await session.execute(select(ReservationCertificate.id).where(
                ReservationCertificate.profile_version_id == latest.id).limit(1))).first() is not None
    items = enforcement.sensitive_items(personal, has_certs)
    if items:
        await enforcement.require_consent(session, user.id, notice.ELIGIBILITY_CHECK, (personal or {}).get("date_of_birth"), items)

async def _save(body: ProfileIn, user: User, session: AsyncSession, carry_over: bool) -> ProfileOut:
    sections = body.model_dump(exclude={"change_summary"}, exclude_none=False)
    # Distinguish "section not sent" (None) from "section sent as empty list"
    sections = {k: v for k, v in sections.items() if k in body.model_fields_set}
    await _require_consent_for(session, user, sections, carry_over)
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

async def _require_consent_for_fact(session: AsyncSession, user: User, version_id: uuid.UUID, subject: str, predicate: str) -> None:
    """A claim about category/caste, DOB, disability, a certificate, service details etc. is that data: storing it (or
    evidence for it) needs ELIGIBILITY_CHECK consent, as the same field in PUT /profile does."""
    items = enforcement.fact_items(subject, predicate)
    if items:
        await enforcement.require_consent(session, user.id, notice.ELIGIBILITY_CHECK,
                                          await enforcement.version_date_of_birth(session, version_id), items)

@router.post("/facts", response_model=FactOut, status_code=status.HTTP_201_CREATED)
async def create_fact(body: FactIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    latest = await ProfileRepository(session).latest_version(user.id)
    if latest is None:
        raise _not_found("profile")
    await _require_consent_for_fact(session, user, latest.id, body.subject, body.predicate)
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
    fact = await _owned_fact(session, user, fact_id)
    await _require_consent_for_fact(session, user, fact.profile_version_id, fact.subject, fact.predicate)
    if body.raw_document_id and await session.get(RawDocument, body.raw_document_id) is None:
        raise _not_found("raw document")
    evidence = await FactRepository(session).add_evidence(fact_id, body.evidence_type, body.raw_document_id, body.source)
    return next(e for e in await _evidence_out(session, fact_id) if e.id == evidence.id)
