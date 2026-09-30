"""Exam preparation: official syllabus and previous year papers (any logged-in user), and the caller's own study plans."""
import json
import uuid
from datetime import date, datetime, timezone
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.api.deps import current_user, get_session
from govcareeros.backend.models.exam_prep import QuestionPaper, StudyPlan, StudyPlanItem
from govcareeros.backend.models.user import User
from govcareeros.backend.services import exam_prep
from govcareeros.backend.services.study_plan import PlanError

router = APIRouter(prefix="/prep", tags=["prep"], dependencies=[Depends(current_user)])

class SubtopicOut(BaseModel):
    text: str
    page: int

class TopicOut(BaseModel):
    order: int
    title: str
    part: Optional[str]
    subtopics: List[SubtopicOut]

class SectionOut(BaseModel):
    name: str
    note: Optional[str]
    topics: List[TopicOut]

class SyllabusOut(BaseModel):
    exam_code: str
    raw_document_id: uuid.UUID
    notice_url: Optional[str]
    retrieved_at: datetime
    parser_version: str
    sections: List[SectionOut]

class PaperOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    exam_code: str
    exam_name: str
    stage: Optional[str]
    year: int
    category: Optional[str]
    paper_name: str
    official_url: str
    size_label: Optional[str]
    raw_document_id: Optional[uuid.UUID]

class PlanIn(BaseModel):
    exam_code: str
    exam_date: date
    weekly_hours: float = Field(gt=0, le=112)
    sections: Optional[List[str]] = None
    start_date: Optional[date] = None  # defaults to today

class PlanItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    position: int
    planned_week: int
    kind: str
    section: Optional[str]
    topic: str
    planned_hours: float
    syllabus_page: Optional[int]
    question_paper_id: Optional[uuid.UUID]
    status: str
    completed_at: Optional[datetime]

class PlanOut(BaseModel):
    id: uuid.UUID
    exam_code: str
    start_date: date
    target_date: date
    weekly_hours: float
    total_weeks: int
    revision_weeks: int
    sections: Optional[List[str]]
    syllabus_raw_document_id: uuid.UUID
    generator_version: str
    items: List[PlanItemOut]

class ItemStatusIn(BaseModel):
    status: Literal["PENDING", "IN_PROGRESS", "DONE", "SKIPPED"]

def _code(code: str) -> str:
    return code.strip().upper()

@router.get("/exams/{code}/syllabus", response_model=SyllabusOut)
async def get_syllabus(code: str, session: AsyncSession = Depends(get_session)):
    raw, rows = await exam_prep.latest_syllabus(session, _code(code))
    if raw is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no official syllabus stored for this exam")
    sections: List[SectionOut] = []
    for r in rows:
        if not sections or sections[-1].name != r.section:
            sections.append(SectionOut(name=r.section, note=r.section_note, topics=[]))
        topics = sections[-1].topics
        if not topics or topics[-1].order != r.topic_order:
            topics.append(TopicOut(order=r.topic_order, title=r.topic, part=r.part, subtopics=[]))
        topics[-1].subtopics.append(SubtopicOut(text=r.subtopic, page=r.page))
    return SyllabusOut(exam_code=_code(code), raw_document_id=raw.id, notice_url=raw.url, retrieved_at=raw.retrieved_at,
                       parser_version=rows[0].parser_version, sections=sections)

@router.get("/exams/{code}/papers", response_model=List[PaperOut])
async def list_papers(code: str, year: Optional[int] = None, downloaded_only: bool = False,
                      session: AsyncSession = Depends(get_session)):
    stmt = select(QuestionPaper).where(QuestionPaper.exam_code == _code(code))
    if year is not None:
        stmt = stmt.where(QuestionPaper.year == year)
    if downloaded_only:
        stmt = stmt.where(QuestionPaper.raw_document_id.is_not(None))
    return (await session.execute(stmt.order_by(QuestionPaper.year.desc(), QuestionPaper.exam_name, QuestionPaper.paper_name))).scalars().all()

async def _plan_out(session: AsyncSession, plan: StudyPlan) -> PlanOut:
    items = (await session.execute(select(StudyPlanItem).where(StudyPlanItem.plan_id == plan.id)
                                   .order_by(StudyPlanItem.position))).scalars().all()
    return PlanOut(
        id=plan.id, exam_code=plan.exam_code, start_date=plan.start_date, target_date=plan.target_date,
        weekly_hours=plan.weekly_hours, total_weeks=plan.total_weeks, revision_weeks=plan.revision_weeks,
        sections=json.loads(plan.sections) if plan.sections else None,
        syllabus_raw_document_id=plan.syllabus_raw_document_id, generator_version=plan.generator_version,
        items=[PlanItemOut.model_validate(i) for i in items],
    )

async def _own_plan(session: AsyncSession, plan_id: uuid.UUID, user: User) -> StudyPlan:
    plan = (await session.execute(select(StudyPlan).where(StudyPlan.id == plan_id, StudyPlan.user_id == user.id))).scalar_one_or_none()
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "study plan not found")
    return plan

@router.post("/plans", response_model=PlanOut, status_code=status.HTTP_201_CREATED)
async def create_plan(body: PlanIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    try:
        plan, _ = await exam_prep.create_study_plan(session, user.id, _code(body.exam_code), body.exam_date,
                                                    body.weekly_hours, body.sections, body.start_date)
    except PlanError as e:
        raise HTTPException(422, str(e))  # the constant was renamed across Starlette versions
    return await _plan_out(session, plan)

@router.get("/plans", response_model=List[PlanOut])
async def list_plans(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    plans = (await session.execute(select(StudyPlan).where(StudyPlan.user_id == user.id)
                                   .order_by(StudyPlan.created_at.desc()))).scalars().all()
    return [await _plan_out(session, p) for p in plans]

@router.get("/plans/{plan_id}", response_model=PlanOut)
async def get_plan(plan_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    return await _plan_out(session, await _own_plan(session, plan_id, user))

@router.patch("/plans/{plan_id}/items/{item_id}", response_model=PlanItemOut)
async def set_item_status(plan_id: uuid.UUID, item_id: uuid.UUID, body: ItemStatusIn,
                          user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    plan = await _own_plan(session, plan_id, user)
    item = (await session.execute(select(StudyPlanItem).where(StudyPlanItem.id == item_id,
                                                              StudyPlanItem.plan_id == plan.id))).scalar_one_or_none()
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "plan item not found")
    item.status = body.status
    item.completed_at = (item.completed_at or datetime.now(timezone.utc)) if body.status == "DONE" else None
    await session.flush()
    await session.refresh(item)
    return item
