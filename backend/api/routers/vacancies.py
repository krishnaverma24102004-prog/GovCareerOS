import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from govcareeros.backend.api.deps import current_user, get_session
from govcareeros.backend.models.artifact import RawDocument
from govcareeros.backend.models.vacancy import Vacancy
from govcareeros.backend.schemas.api import VacancyDetail, VacancySummary

router = APIRouter(prefix="/vacancies", tags=["vacancies"], dependencies=[Depends(current_user)])

@router.get("", response_model=List[VacancySummary])
async def list_vacancies(
    q: Optional[str] = Query(None, description="substring of post name, ministry or department"),
    limit: int = Query(50, le=200), offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Vacancy).order_by(Vacancy.application_deadline.desc().nulls_last(), Vacancy.post_name)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Vacancy.post_name.ilike(like) | Vacancy.ministry.ilike(like) | Vacancy.department.ilike(like))
    return (await session.execute(stmt.limit(limit).offset(offset))).scalars().all()

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
    })
