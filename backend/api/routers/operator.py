"""
Operator console (cyber-cafe / CSC operators, B2B2C). Operator-only; every client lookup is scoped to the calling
operator and another operator's client is a 404. Rules live in backend/services/operator_console.py; billing is
TEST MODE only (backend/services/billing.py).
"""
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.api.deps import current_user, get_session
from govcareeros.backend.models.consent import ConsentRecord
from govcareeros.backend.models.operator import LedgerEntry, OperatorReport
from govcareeros.backend.models.user import User
from govcareeros.backend.parsing.upsc_advertisement import IST
from govcareeros.backend.repositories.profile_repository import ProfileRepository
from govcareeros.backend.schemas.profile import ProfileIn, ProfileOut, ProfileVersionSummary
from govcareeros.backend.services import billing, consent
from govcareeros.backend.services import operator_console as oc
from govcareeros.backend.services.application_prep import PrepInputError, build_checklist, build_exam_checklist
from govcareeros.backend.services.profile_service import ProfileService

router = APIRouter(prefix="/operator", tags=["operator"])

async def require_operator(user: User = Depends(current_user)) -> User:
    if not user.is_operator or user.managed_by_id is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "operator role required")
    return user

def _http(e: oc.OperatorError) -> HTTPException:
    return HTTPException(e.status, str(e))

class ConsentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # The operator confirms the client is at the counter and the notice was read out to them
    client_present: bool
    notice_version: str
    data_items: List[str] = Field(min_length=1)
    guardian_name: Optional[str] = Field(None, max_length=255, description="parent / lawful guardian of a client under 18")

class ClientIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: Optional[str] = Field(None, max_length=255)
    consent: ConsentIn

class ConsentOut(BaseModel):
    id: uuid.UUID
    purpose: str
    data_items: List[str]
    notice_version: str
    method: str
    granted_at: datetime
    withdrawn_at: Optional[datetime]
    guardian_name: Optional[str]

class ClientOut(BaseModel):
    id: uuid.UUID
    full_name: Optional[str]
    created_at: datetime
    claimed: bool
    consent: Optional[ConsentOut]

class ChargeOut(BaseModel):
    id: uuid.UUID
    amount_paise: int
    currency: str
    provider: str
    provider_ref: str
    test_mode: bool
    at: datetime

class ReportOut(BaseModel):
    id: uuid.UUID
    client_id: uuid.UUID
    application_id: uuid.UUID
    kind: str
    target_id: uuid.UUID
    profile_version_id: Optional[uuid.UUID]
    snapshot: Dict[str, Any]
    created_at: datetime
    charge: Optional[ChargeOut]

class ClientDetail(ClientOut):
    profile_versions: List[ProfileVersionSummary]
    reports: List[ReportOut]

class ReportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["vacancy", "exam"]
    target_id: uuid.UUID
    profile_version_id: Optional[uuid.UUID] = None
    idempotency_key: str = Field(min_length=8, max_length=80)

class ClaimTokenOut(BaseModel):
    token: str
    expires_at: datetime
    note: str = "Give this one-time code to the client; it is shown only once."

def _consent_out(r: Optional[ConsentRecord]) -> Optional[ConsentOut]:
    if r is None:
        return None
    return ConsentOut(id=r.id, purpose=r.purpose, data_items=json.loads(r.data_items), notice_version=r.notice_version,
                      method=r.method, granted_at=r.granted_at, withdrawn_at=r.withdrawn_at, guardian_name=r.guardian_name)

async def _client_out(session: AsyncSession, client: User) -> ClientOut:
    return ClientOut(id=client.id, full_name=client.full_name, created_at=client.created_at, claimed=client.claimed_at is not None,
                     consent=_consent_out(await consent.active_consent(session, client.id, oc.PURPOSE)))

def _report_out(r: OperatorReport, e: Optional[LedgerEntry]) -> ReportOut:
    return ReportOut(id=r.id, client_id=r.client_id, application_id=r.application_id, kind=r.kind,
                     target_id=r.vacancy_id or r.exam_id, profile_version_id=r.profile_version_id, snapshot=json.loads(r.snapshot),
                     created_at=r.created_at,
                     charge=ChargeOut(id=e.id, amount_paise=e.amount_paise, currency=e.currency, provider=e.provider,
                                      provider_ref=e.provider_ref, test_mode=e.test_mode, at=e.at) if e else None)

@router.get("/consent-notice")
async def consent_notice(operator: User = Depends(require_operator)) -> Dict[str, Any]:
    """The notice the operator reads out to the client, its version and the data items the client can agree to."""
    return oc.consent_notice()

@router.get("/clients", response_model=List[ClientOut])
async def list_clients(operator: User = Depends(require_operator), session: AsyncSession = Depends(get_session)):
    return [await _client_out(session, c) for c in await oc.list_clients(session, operator)]

@router.post("/clients", response_model=ClientOut, status_code=status.HTTP_201_CREATED)
async def create_client(body: ClientIn, operator: User = Depends(require_operator), session: AsyncSession = Depends(get_session)):
    try:
        client, _ = await oc.create_client(session, operator, body.full_name, **body.consent.model_dump())
    except oc.OperatorError as e:
        raise _http(e)
    await session.refresh(client)
    return await _client_out(session, client)

async def _client(session: AsyncSession, operator: User, client_id: uuid.UUID) -> User:
    try:
        return await oc.own_client(session, operator, client_id)
    except oc.OperatorError as e:
        raise _http(e)

@router.get("/clients/{client_id}", response_model=ClientDetail)
async def get_client(client_id: uuid.UUID, operator: User = Depends(require_operator), session: AsyncSession = Depends(get_session)):
    client = await _client(session, operator, client_id)
    base = await _client_out(session, client)
    # Profile versions are client data: listed only under an active consent
    versions = await ProfileRepository(session).list_versions(client.id) if base.consent else []
    return ClientDetail(**base.model_dump(), profile_versions=[ProfileVersionSummary.model_validate(v) for v in versions],
                        reports=[_report_out(r, e) for r, e in await oc.client_reports(session, operator, client)])

@router.post("/clients/{client_id}/consent", response_model=ConsentOut, status_code=status.HTTP_201_CREATED)
async def grant_consent(client_id: uuid.UUID, body: ConsentIn, operator: User = Depends(require_operator),
                        session: AsyncSession = Depends(get_session)):
    client = await _client(session, operator, client_id)
    try:
        return _consent_out(await oc.regrant_consent(session, operator, client, **body.model_dump()))
    except oc.OperatorError as e:
        raise _http(e)

@router.post("/clients/{client_id}/consent/withdraw", status_code=status.HTTP_204_NO_CONTENT)
async def withdraw_consent(client_id: uuid.UUID, operator: User = Depends(require_operator), session: AsyncSession = Depends(get_session)):
    await oc.withdraw_consent(session, await _client(session, operator, client_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)

@router.get("/clients/{client_id}/profile", response_model=ProfileOut)
async def get_client_profile(client_id: uuid.UUID, operator: User = Depends(require_operator), session: AsyncSession = Depends(get_session)):
    client = await _client(session, operator, client_id)
    try:
        record = await oc.require_consent(session, client)
    except oc.OperatorError as e:
        raise _http(e)
    repo = ProfileRepository(session)
    latest = await repo.latest_version(client.id)
    if latest is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "profile not found")
    return ProfileOut(version=ProfileVersionSummary.model_validate(latest), **_consented(await repo.load_sections(latest.id), record))

def _consented(sections: Dict[str, Any], record: ConsentRecord) -> Dict[str, Any]:
    """Only the sections the active consent covers; a section the client has since left out reads as empty."""
    items = set(json.loads(record.data_items))
    return {k: v if oc.SECTION_ITEMS.get(k) in items else (None if k == "personal_info" else []) for k, v in sections.items()}

async def _save_profile(client_id: uuid.UUID, body: ProfileIn, operator: User, session: AsyncSession, carry_over: bool) -> ProfileOut:
    client = await _client(session, operator, client_id)
    sections = {k: v for k, v in body.model_dump(exclude={"change_summary"}).items() if k in body.model_fields_set}
    try:
        # Checked before anything is written: every section sent must be covered by the client's consent
        record = await oc.require_consent(session, client, oc.profile_items(sections))
    except oc.OperatorError as e:
        raise _http(e)
    summary = body.change_summary or f"entered by operator {operator.id} with the client present"
    version = await ProfileService(session).save_profile(client.id, sections, summary, carry_over)
    repo = ProfileRepository(session)
    return ProfileOut(version=ProfileVersionSummary.model_validate(version), **_consented(await repo.load_sections(version.id), record))

@router.put("/clients/{client_id}/profile", response_model=ProfileOut, status_code=status.HTTP_201_CREATED)
async def replace_client_profile(client_id: uuid.UUID, body: ProfileIn, operator: User = Depends(require_operator),
                                 session: AsyncSession = Depends(get_session)):
    return await _save_profile(client_id, body, operator, session, carry_over=False)

@router.patch("/clients/{client_id}/profile", response_model=ProfileOut, status_code=status.HTTP_201_CREATED)
async def update_client_profile(client_id: uuid.UUID, body: ProfileIn, operator: User = Depends(require_operator),
                                session: AsyncSession = Depends(get_session)):
    return await _save_profile(client_id, body, operator, session, carry_over=True)

@router.post("/clients/{client_id}/claim-token", response_model=ClaimTokenOut, status_code=status.HTTP_201_CREATED)
async def claim_token(client_id: uuid.UUID, operator: User = Depends(require_operator), session: AsyncSession = Depends(get_session)):
    client = await _client(session, operator, client_id)
    try:
        token, expires = await oc.issue_claim_token(session, operator, client)
    except oc.OperatorError as e:
        raise _http(e)
    return ClaimTokenOut(token=token, expires_at=expires)

@router.post("/clients/{client_id}/reports", response_model=ReportOut)
async def create_report(client_id: uuid.UUID, body: ReportIn, response: Response, operator: User = Depends(require_operator),
                        session: AsyncSession = Depends(get_session)):
    """201 with a new report and its single TEST-MODE charge; the same idempotency_key again returns 200 and that report."""
    client = await _client(session, operator, client_id)
    try:
        report, entry, created = await oc.create_report(session, operator, client, body.kind, body.target_id,
                                                        body.idempotency_key, body.profile_version_id)
    except oc.OperatorError as e:
        raise _http(e)
    await session.refresh(report)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return _report_out(report, entry)

@router.get("/reports/{report_id}", response_model=ReportOut)
async def get_report(report_id: uuid.UUID, operator: User = Depends(require_operator), session: AsyncSession = Depends(get_session)):
    try:
        report = await oc.own_report(session, operator, report_id)
    except oc.OperatorError as e:
        raise _http(e)
    return _report_out(report, await billing.charge_for_report(session, report.id))

@router.get("/reports/{report_id}/checklist")
async def report_checklist(report_id: uuid.UUID, operator: User = Depends(require_operator),
                           session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """The client's personalised checklist (application_prep) for the report's vacancy or exam, every item cited."""
    try:
        report = await oc.own_report(session, operator, report_id)
        client = await oc.own_client(session, operator, report.client_id)
        await oc.require_consent(session, client)
    except oc.OperatorError as e:
        raise _http(e)
    if report.profile_version_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "the report has no profile version; enter the client's profile and make a new report")
    try:
        if report.kind == "vacancy":
            return await build_checklist(session, client.id, report.vacancy_id, report.profile_version_id)
        return await build_exam_checklist(session, client.id, report.exam_id, report.profile_version_id)
    except PrepInputError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))

@router.get("/statement")
async def monthly_statement(month: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}$", description="YYYY-MM (IST); defaults to this month"),
                            operator: User = Depends(require_operator), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    month = month or datetime.now(timezone.utc).astimezone(IST).strftime("%Y-%m")
    try:
        return await billing.statement(session, operator.id, month)
    except ValueError as e:
        raise HTTPException(422, str(e))
