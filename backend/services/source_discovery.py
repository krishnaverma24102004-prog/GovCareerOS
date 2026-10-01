"""
Registry sync + notice-level discovery across every registered official source.

Network probes run concurrently; database writes happen sequentially on one session.
"""
import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.models.notice import RecruitmentNotice
from govcareeros.backend.models.source import GovernmentSource
from govcareeros.backend.sources.generic_notices import PageFetcher, ProbeResult
from govcareeros.backend.sources import ssc
from govcareeros.backend.sources.notice_classifier import classify_notice
from govcareeros.backend.sources.registry import SOURCES, SourceSpec
from govcareeros.backend.sources.url_guard import UrlPolicy

@dataclass
class SourceDiscoverySummary:
    name: str
    status: str
    fetch_mode: str
    page_url: Optional[str]
    notices_seen: int
    new_notices: int
    new_vacancy_notices: int
    detail: Optional[str]

def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()

def _config(source: GovernmentSource) -> Dict:
    try:
        return json.loads(source.configuration) if source.configuration else {}
    except json.JSONDecodeError:
        return {}

async def sync_registry(session: AsyncSession, specs: Iterable[SourceSpec] = SOURCES) -> Dict[str, GovernmentSource]:
    """Insert missing sources; existing rows keep their health, discovery history and reviewer-set reuse policy."""
    by_name: Dict[str, GovernmentSource] = {}
    existing = {s.name: s for s in (await session.execute(select(GovernmentSource))).scalars()}
    for spec in specs:
        source = existing.get(spec.name)
        if source is None:
            source = GovernmentSource(
                name=spec.name, organization=spec.organization, category=spec.category,
                official_domain=spec.official_domain, recruitment_url=spec.recruitment_url,
                source_type="DOCUMENT", adapter_name=spec.parser, enabled=True, jurisdiction=spec.jurisdiction,
            )
            session.add(source)
        else:
            if source.recruitment_url != spec.recruitment_url:
                # A page resolved from the old URL would otherwise keep overriding the corrected one
                config = _config(source)
                if config.pop("resolved_url", None) is not None:
                    source.configuration = json.dumps(config)
            source.recruitment_url = spec.recruitment_url
            # The fetch policy is derived from the domain, so it must follow the registry
            source.official_domain = spec.official_domain
            source.adapter_name = spec.parser or source.adapter_name
            source.jurisdiction = spec.jurisdiction
        # Fetch mode follows the registry too (the probe reads it from the stored configuration)
        config = _config(source)
        if bool(config.get("force_browser")) != spec.force_browser:
            if spec.force_browser:
                config["force_browser"] = True
            else:
                del config["force_browser"]
            source.configuration = json.dumps(config)
        by_name[spec.name] = source
    await session.flush()
    return by_name

def source_policy(source: GovernmentSource) -> UrlPolicy:
    return UrlPolicy.for_source(source.official_domain, source.recruitment_url, source.category)

def _probe_url(source: GovernmentSource) -> str:
    # Reuse a page the locator found earlier, so we don't re-search every run (never an off-domain one)
    resolved = _config(source).get("resolved_url")
    return resolved if resolved and source_policy(source).allows(resolved) else source.recruitment_url

def _classify(source: GovernmentSource, title: str, url: str) -> str:
    return ssc.classify(title, url) if source.adapter_name == "ssc" else classify_notice(title, url)

async def reclassify_notices(session: AsyncSession) -> Dict[Tuple[str, str], int]:
    """Re-apply the (deterministic) classifier to every stored notice, so classifier fixes also reach notices
    that are no longer on any listing page. Returns {(old_kind, new_kind): count} for the rows it changed."""
    changed: Dict[Tuple[str, str], int] = {}
    rows = (await session.execute(select(RecruitmentNotice, GovernmentSource)
                                  .join(GovernmentSource, GovernmentSource.id == RecruitmentNotice.source_id))).all()
    for notice, source in rows:
        kind = _classify(source, notice.title, notice.url)
        if kind != notice.notice_kind:
            changed[(notice.notice_kind, kind)] = changed.get((notice.notice_kind, kind), 0) + 1
            notice.notice_kind = kind
    await session.flush()
    return changed

async def probe_sources(fetcher: PageFetcher, sources: List[GovernmentSource], concurrency: int = 6) -> List[ProbeResult]:
    sem = asyncio.Semaphore(concurrency)

    async def one(source: GovernmentSource) -> ProbeResult:
        async with sem:
            try:
                if source.adapter_name == "ssc":
                    return await ssc.probe(fetcher, allow_browser=getattr(fetcher, "allow_browser", False))
                return await fetcher.discover(_probe_url(source), source_policy(source),
                                             browser=bool(_config(source).get("force_browser")))
            except Exception as e:  # one broken site must not stop the sweep
                return ProbeResult("UNREACHABLE", detail=f"{type(e).__name__}: {str(e)[:200]}")

    return await asyncio.gather(*(one(s) for s in sources))

async def record_probe(session: AsyncSession, source: GovernmentSource, result: ProbeResult, now: datetime) -> SourceDiscoverySummary:
    source.health_status = "HEALTHY" if result.status == "OK" else result.status
    source.last_verified = now
    if result.status == "OK":
        source.last_success = now
    else:
        source.last_failure = now
    config = _config(source)
    config.update({
        "last_probe_status": result.status, "http_status": result.http_status,
        "fetch_mode": result.fetch_mode, "detail": result.detail,
    })
    if result.status == "OK" and result.final_url and source_policy(source).allows(result.final_url):
        config["resolved_url"] = result.final_url
    elif config.get("resolved_url") and not source_policy(source).allows(config["resolved_url"]):
        del config["resolved_url"]
    source.configuration = json.dumps(config)

    new = new_vacancy = 0
    if result.notices:
        hashes = [url_hash(n.url) for n in result.notices]
        known = {
            n.url_hash: n for n in (await session.execute(
                select(RecruitmentNotice).where(RecruitmentNotice.source_id == source.id, RecruitmentNotice.url_hash.in_(hashes))
            )).scalars()
        }
        for link, h in zip(result.notices, hashes):
            if h in known:
                known[h].last_seen_at = now
                # Classification is deterministic; re-apply so classifier fixes reach stored notices
                known[h].notice_kind = _classify(source, link.title, link.url)
                continue
            kind = _classify(source, link.title, link.url)
            session.add(RecruitmentNotice(
                source_id=source.id, url=link.url, url_hash=h, title=link.title,
                document_type=link.document_type, notice_kind=kind, found_on_url=result.final_url,
                first_seen_at=now, last_seen_at=now,
                # Only sources with a vacancy parser get automatic extraction; the rest need a human
                review_status="NEW" if source.adapter_name else "MANUAL_REVIEW",
            ))
            new += 1
            new_vacancy += kind in ("VACANCY", "CORRIGENDUM")
    await session.flush()
    return SourceDiscoverySummary(
        source.name, result.status, result.fetch_mode, result.final_url,
        len(result.notices), new, new_vacancy, result.detail,
    )

async def discover_all(
    session: AsyncSession, fetcher: PageFetcher, specs: Iterable[SourceSpec] = SOURCES, concurrency: int = 6,
) -> List[SourceDiscoverySummary]:
    sources = list((await sync_registry(session, specs)).values())
    results = await probe_sources(fetcher, sources, concurrency)
    now = datetime.now(timezone.utc)
    return [await record_probe(session, s, r, now) for s, r in zip(sources, results)]
