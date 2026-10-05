import uuid
from dataclasses import asdict
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from govcareeros.backend.api.deps import current_user, get_session
from govcareeros.backend.models.artifact import RawDocument
from govcareeros.backend.models.user import User
from govcareeros.backend.models.vacancy import Vacancy, VacancyVersion
from govcareeros.backend.schemas.api import VacancyDetail, VacancySummary
from govcareeros.backend.schemas.attribution import DISCLAIMER
from govcareeros.backend.services import attribution, change_log, deadline_helper

router = APIRouter(prefix="/vacancies", tags=["vacancies"], dependencies=[Depends(current_user)])

@router.get("", response_model=List[VacancySummary])
async def list_vacancies(
    q: Optional[str] = Query(None, description="substring of post name, ministry or department"),
    recruitment_mode: Optional[Literal["DIRECT", "DEPUTATION", "CONTRACT", "WALK_IN", "UNKNOWN"]] = None,
    limit: int = Query(50, le=200), offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Vacancy).order_by(Vacancy.application_deadline.desc().nulls_last(), Vacancy.post_name)
    if recruitment_mode:
        stmt = stmt.where(Vacancy.recruitment_mode == recruitment_mode)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Vacancy.post_name.ilike(like) | Vacancy.ministry.ilike(like) | Vacancy.department.ilike(like))
    vacancies = (await session.execute(stmt.limit(limit).offset(offset))).scalars().all()
    return [VacancySummary.model_validate(v).model_copy(update={"attribution": a})
            for v, a in zip(vacancies, await attribution.for_vacancies(session, vacancies))]

@router.get("/{vacancy_id}", response_model=VacancyDetail)
async def get_vacancy(vacancy_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    res = await session.execute(
        select(Vacancy).options(selectinload(Vacancy.requirements), selectinload(Vacancy.provenance))
        .where(Vacancy.id == vacancy_id)
    )
    vacancy = res.scalar_one_or_none()
    if vacancy is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "vacancy not found")
    # Filter on the response, never on the ORM relationship (that would detach rows on commit)
    detail = VacancyDetail.model_validate(vacancy)
    active = {r.id for r in vacancy.requirements if r.superseded_at is None}
    doc_ids = {p.raw_document_id for p in detail.provenance}
    urls = dict((await session.execute(select(RawDocument.id, RawDocument.url).where(RawDocument.id.in_(doc_ids)))).all()) if doc_ids else {}
    return detail.model_copy(update={
        "requirements": [r for r in detail.requirements if r.id in active],
        "provenance": [p.model_copy(update={"document_url": urls.get(p.raw_document_id)}) for p in detail.provenance],
        "attribution": (await attribution.for_vacancies(session, [vacancy]))[0],
    })

async def _vacancy(session: AsyncSession, vacancy_id: uuid.UUID) -> Vacancy:
    vacancy = await session.get(Vacancy, vacancy_id)
    if vacancy is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "vacancy not found")
    return vacancy

@router.get("/{vacancy_id}/changes")
async def list_changes(vacancy_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> List[Dict[str, Any]]:
    """Field-level change log from official notices, oldest first, with the earlier and the new value's page and quote."""
    vacancy = await _vacancy(session, vacancy_id)
    return [{**asdict(vc), "lines": change_log.change_lines(vc), "disclaimer": DISCLAIMER}
            for vc in await change_log.vacancy_change_log(session, vacancy)]

@router.get("/{vacancy_id}/deadline")
async def deadline(vacancy_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """The official closing date and its evidence; a forwarding estimate for in-service applicants is separate and labelled."""
    vacancy = await _vacancy(session, vacancy_id)
    _, evidence = await change_log.vacancy_current_evidence(session, vacancy, "application_deadline")
    raw_ids = [vacancy.raw_document_id] + list((await session.execute(
        select(VacancyVersion.raw_document_id).where(VacancyVersion.vacancy_id == vacancy.id))).scalars())
    return await deadline_helper.deadline_info(session, user.id, vacancy.application_deadline, evidence, raw_ids)
