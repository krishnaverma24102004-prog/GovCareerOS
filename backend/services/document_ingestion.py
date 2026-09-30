"""
Official document -> RawDocument -> parsed advertisement -> canonical Vacancy (+ provenance).

Idempotent: the same PDF bytes from the same source are stored once, and a vacancy
already ingested under the same source + external_ref is not duplicated.
"""
import hashlib
import uuid
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.config.settings import settings
from govcareeros.backend.documents.pdf_text import extract_pages
from govcareeros.backend.models.artifact import RawDocument
from govcareeros.backend.models.source import GovernmentSource
from govcareeros.backend.models.vacancy import Vacancy, VacancyFieldProvenance, VacancyRequirement, VacancyVersion
from govcareeros.backend.parsing.upsc_advertisement import ParsedAdvertisement, ParsedField, parse_advertisement
from govcareeros.backend.services.application_prep import store_application_requirements
from govcareeros.backend.sources import upsc

# Fields whose presence makes a parsed vacancy usable for eligibility
_CORE_FIELDS = ("post_name", "vacancy_count", "application_deadline")
_CORE_REQUIREMENTS = ("EDUCATION", "AGE_MAX")

@dataclass
class DocumentIngestionResult:
    raw_document_id: Any
    raw_document_created: bool
    advertisement_number: Optional[str]
    created_vacancy_ids: List[Any] = field(default_factory=list)
    existing_vacancy_refs: List[str] = field(default_factory=list)
    reparsed_vacancy_ids: List[Any] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

@dataclass
class PipelineRunResult:
    source_health: str
    documents: List[DocumentIngestionResult] = field(default_factory=list)
    notices: List[Any] = field(default_factory=list)  # NoticeApplication
    errors: List[str] = field(default_factory=list)

async def get_or_create_source(session: AsyncSession, spec: Dict[str, Any]) -> GovernmentSource:
    res = await session.execute(select(GovernmentSource).where(GovernmentSource.name == spec["name"]))
    source = res.scalar_one_or_none()
    if source is None:
        source = GovernmentSource(**spec)
        session.add(source)
        await session.flush()
    return source

async def store_raw_document(
    session: AsyncSession, source: GovernmentSource, url: str, body: bytes,
    status: int, content_type: str, discovery_method: str = "PDF",
) -> Tuple[RawDocument, bool]:
    content_hash = "sha256:" + hashlib.sha256(body).hexdigest()
    res = await session.execute(
        select(RawDocument).where(RawDocument.source_id == source.id, RawDocument.content_hash == content_hash)
    )
    existing = res.scalar_one_or_none()
    if existing is not None:
        return existing, False

    path = Path(settings.storage_dir) / "raw_documents" / source.name.lower() / (content_hash.split(":", 1)[1] + ".pdf")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(body)

    raw = RawDocument(
        source_id=source.id,
        retrieved_at=datetime.now(timezone.utc),
        url=url,
        http_method="GET",
        status=status,
        content_type=content_type,
        content_hash=content_hash,
        discovery_method=discovery_method,
        adapter_version=source.adapter_name,
        storage_path=str(path),
    )
    session.add(raw)
    await session.flush()
    return raw, True

def _as_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)

def _confidence(fields: Dict[str, ParsedField], req_types: set) -> float:
    present = sum(1 for f in _CORE_FIELDS if fields.get(f) and fields[f].value is not None)
    present += sum(1 for r in _CORE_REQUIREMENTS if r in req_types)
    return round(present / (len(_CORE_FIELDS) + len(_CORE_REQUIREMENTS)), 2)

async def apply_official_notices(session: AsyncSession, vacancy: Vacancy) -> None:
    """Sets the vacancy's deadline and status to what the latest official notice says.

    Recomputed from the advertisement's own closing date plus every VacancyVersion, ordered by notice
    date then by when the notice was retrieved, so neither a reparse nor the order notices are found in
    decides the result. Undated notices count as older than dated ones.
    """
    base = (await session.execute(select(VacancyFieldProvenance.value).where(
        VacancyFieldProvenance.vacancy_id == vacancy.id, VacancyFieldProvenance.raw_document_id == vacancy.raw_document_id,
        VacancyFieldProvenance.field_name == "application_deadline"))).scalar_one_or_none()
    rows = (await session.execute(
        select(VacancyVersion.notice_date, VacancyVersion.details, RawDocument.retrieved_at)
        .outerjoin(RawDocument, RawDocument.id == VacancyVersion.raw_document_id)
        .where(VacancyVersion.vacancy_id == vacancy.id)
    )).all()
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    deadline = datetime.fromisoformat(base) if base else vacancy.application_deadline
    for _, details, _ in sorted(rows, key=lambda r: (r.notice_date or oldest, r.retrieved_at or oldest)):
        details = json.loads(details or "{}")
        if details.get("deadline_change", {}).get("new"):
            deadline = datetime.fromisoformat(details["deadline_change"]["new"])
        if "cancellation" in details:
            vacancy.status = "CANCELLED"
    vacancy.application_deadline = deadline
    await session.flush()

async def ingest_advertisement(
    session: AsyncSession, source: GovernmentSource, raw: RawDocument, parsed: ParsedAdvertisement,
    source_url: Optional[str] = None,
) -> DocumentIngestionResult:
    ad = parsed.fields
    advt_no = ad["advertisement_number"].value if "advertisement_number" in ad else None
    result = DocumentIngestionResult(raw.id, False, advt_no, warnings=list(parsed.warnings))
    raw.parser_version = parsed.parser_version

    for pv in parsed.vacancies:
        res = await session.execute(
            select(Vacancy).where(Vacancy.source_id == source.id, Vacancy.external_ref == pv.external_ref)
        )
        existing = res.scalar_one_or_none()
        if existing is not None:
            if existing.raw_document_id != raw.id:
                # Same vacancy in a different document = corrigendum/re-issue; not applied until change tracking exists
                result.warnings.append(f"vacancy {pv.external_ref}: already stored from another document; change not applied")
                result.existing_vacancy_refs.append(pv.external_ref)
                continue
            if existing.parser_version == parsed.parser_version:
                result.existing_vacancy_refs.append(pv.external_ref)
                continue

        # Advertisement-wide fields (dates, mode) apply to every vacancy in it
        fields: Dict[str, ParsedField] = {**ad, **pv.fields}
        if "post_name" not in fields:
            result.warnings.append(f"vacancy {pv.external_ref}: skipped, post name not parsed")
            continue

        req_types = {r.req_type for r in pv.requirements}
        values = dict(
            organization=source.organization,
            ministry=pv.value("ministry"),
            department=pv.value("department"),
            post_name=pv.value("post_name"),
            vacancy_count=pv.value("vacancy_count"),
            advertisement_number=advt_no,
            external_ref=pv.external_ref,
            pay_scale=pv.value("pay_scale"),
            headquarters=pv.value("headquarters"),
            application_mode=ad["application_mode"].value if "application_mode" in ad else "UNKNOWN",
            application_start=ad["application_start"].value if "application_start" in ad else None,
            application_deadline=ad["application_deadline"].value if "application_deadline" in ad else None,
            eligibility_crucial_date=ad["eligibility_crucial_date"].value if "eligibility_crucial_date" in ad else None,
            notification_url=raw.url,
            application_url=ad["application_url"].value if "application_url" in ad else None,
            source_url=source_url,
            source_id=source.id,
            raw_document_id=raw.id,
            parser_version=parsed.parser_version,
            confidence=_confidence(fields, req_types),
            recruitment_mode=pv.value("recruitment_mode") or "UNKNOWN",
        )
        if existing is not None:
            # Same document, newer parser: keep the vacancy id stable. Old requirement rows are superseded,
            # not deleted, because past eligibility decisions cite them as evidence.
            await session.execute(delete(VacancyFieldProvenance).where(VacancyFieldProvenance.vacancy_id == existing.id))
            await session.execute(
                update(VacancyRequirement)
                .where(VacancyRequirement.vacancy_id == existing.id, VacancyRequirement.superseded_at.is_(None),
                       # conditions added by later official notices are not part of the advertisement parse
                       VacancyRequirement.req_type != "NOTICE_CONDITION")
                .values(superseded_at=datetime.now(timezone.utc))
            )
            for key, val in values.items():
                setattr(existing, key, val)
            vacancy = existing
            result.reparsed_vacancy_ids.append(vacancy.id)
        else:
            vacancy = Vacancy(id=uuid.uuid4(), **values)
            session.add(vacancy)
            result.created_vacancy_ids.append(vacancy.id)
        await session.flush()

        for name, pf in fields.items():
            session.add(VacancyFieldProvenance(
                vacancy_id=vacancy.id, field_name=name, value=_as_text(pf.value), raw_document_id=raw.id,
                page=pf.provenance.page, section=pf.provenance.section, excerpt=pf.provenance.excerpt,
            ))
        # Vacancy-specific requirements plus the advertisement's general rules (citizenship, relaxations)
        for req in pv.requirements + parsed.general_requirements:
            session.add(VacancyRequirement(
                vacancy_id=vacancy.id, req_type=req.req_type, value=_as_text(req.value), is_mandatory=req.is_mandatory,
                raw_document_id=raw.id, source_page=req.provenance.page,
                source_section=req.provenance.section, source_excerpt=req.provenance.excerpt,
            ))
        await session.flush()
        if existing is not None:
            # The parse restored the advertisement's own dates; notices already applied still stand
            await apply_official_notices(session, vacancy)

    return result

async def ingest_pdf_bytes(
    session: AsyncSession, source: GovernmentSource, url: str, body: bytes,
    status: int = 200, content_type: str = "application/pdf", source_url: Optional[str] = None,
) -> DocumentIngestionResult:
    raw, created = await store_raw_document(session, source, url, body, status, content_type)
    pages = extract_pages(body)
    parsed = parse_advertisement(pages)
    result = await ingest_advertisement(session, source, raw, parsed, source_url=source_url)
    # Documents, fee and how-to-apply instructions from the same advertisement (backend/services/application_prep.py)
    app_info = await store_application_requirements(session, source, raw, pages, result.advertisement_number)
    result.warnings += [f"application instructions: {w}" for w in app_info.warnings]
    result.raw_document_created = created
    return result

async def run_upsc_pipeline(session: AsyncSession, client: Optional[httpx.AsyncClient] = None) -> PipelineRunResult:
    source = await get_or_create_source(session, upsc.UPSC_SOURCE)
    now = datetime.now(timezone.utc)
    owns_client = client is None
    client = client or upsc.make_client()
    run = PipelineRunResult(source_health="UNKNOWN")
    try:
        links = await upsc.discover_advertisements(client)
        if not links:
            run.errors.append("no advertisement PDFs found on listing page")
        for link in links:
            try:
                doc = await upsc.download_pdf(client, link.url)
                # Savepoint per document: a failure rolls back only this PDF's rows
                async with session.begin_nested():
                    run.documents.append(await ingest_pdf_bytes(
                        session, source, doc.url, doc.body, doc.status, doc.content_type, source_url=upsc.LISTING_URL,
                    ))
            except upsc.AccessRestricted:
                raise
            except Exception as e:  # one bad PDF must not abort the others
                run.errors.append(f"{link.url}: {type(e).__name__}: {e}")
        # Official notices (clarifications, corrigenda, extensions) are applied after the advertisements
        from govcareeros.backend.services.notice_ingestion import ingest_notice_pdf
        for notice in await upsc.discover_notices(client):
            try:
                doc = await upsc.download_pdf(client, notice.url)
                async with session.begin_nested():
                    run.notices.append(await ingest_notice_pdf(session, source, doc.url, doc.body, notice.title, doc.status, doc.content_type))
            except upsc.AccessRestricted:
                raise
            except Exception as e:
                run.errors.append(f"{notice.url}: {type(e).__name__}: {e}")
        run.source_health = "HEALTHY" if links and not run.errors else "DEGRADED"
        source.last_success = now
    except upsc.AccessRestricted as e:
        run.source_health = "ACCESS_RESTRICTED"
        run.errors.append(str(e))
        source.last_failure = now
    except upsc.RobotsDisallowed as e:
        run.source_health = "ROBOTS_DISALLOWED"
        run.errors.append(str(e))
        source.last_failure = now
    except httpx.HTTPError as e:
        run.source_health = "UNREACHABLE"
        run.errors.append(f"{type(e).__name__}: {e}")
        source.last_failure = now
    finally:
        if owns_client:
            await client.aclose()
    source.health_status = run.source_health
    source.last_verified = now
    await session.flush()
    return run
