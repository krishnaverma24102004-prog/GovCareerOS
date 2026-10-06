"""
Registry sync + notice-level discovery across every registered official source.

Network probes run concurrently; database writes happen sequentially on one session.

A source with a listing adapter (backend/sources/adapters, looked up by registry name) has its fetched listing
page(s) read by the adapter as well: the configured / located page, then the adapter's LISTING_URLS, each fetched
through the same PageFetcher (url_guard policy + robots.txt). The generic link extraction stays the fallback: its
links are kept, and where an adapter row has the same URL the row wins on every field it states (title,
published_on, last_date, advertisement_number, post_names, kind_hint), with the listing text in listing_evidence.
A row without a document of its own (its url is the listing page, e.g. EMPLOYMENT-NEWS) is told apart by its
own text (listing_row_key), hashed into url_hash with the url; no document URL is made up for it.
"""
import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from types import ModuleType
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urldefrag

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.models.notice import RecruitmentNotice
from govcareeros.backend.models.source import GovernmentSource
from govcareeros.backend.sources.adapters import all_adapters
from govcareeros.backend.sources.adapters.base import ListingRow
from govcareeros.backend.sources.generic_notices import PageFetcher, ProbeResult, _doc_type, analyze
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

def url_hash(url: str, row_key: Optional[str] = None) -> str:
    """Identity of a notice within its source: its URL, plus the row key for a row that has no URL of its own."""
    return hashlib.sha256((url if row_key is None else f"{url}\n{row_key}").encode("utf-8")).hexdigest()

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

# A listing's own label (ListingRow.kind_hint) in the classifier's vocabulary: admit cards and answer keys are
# RESULT there as well (notice_classifier). A label outside this map leaves the notice to the classifier.
KIND_HINT_KINDS = {
    "VACANCY": "VACANCY", "CORRIGENDUM": "CORRIGENDUM", "RESULT": "RESULT", "RULES": "RULES",
    "ADMIT_CARD": "RESULT", "ANSWER_KEY": "RESULT", "SCHEDULE": "EXAM_SCHEDULE",
}

def _evidence(notice: RecruitmentNotice) -> Dict[str, str]:
    try:
        return json.loads(notice.listing_evidence) if notice.listing_evidence else {}
    except json.JSONDecodeError:
        return {}

def _classify(source: GovernmentSource, title: str, url: str, kind_hint: Optional[str] = None,
              kind_hint_text: Optional[str] = None) -> str:
    # The listing's label decides only where the page states it (the adapter quotes the text); else the classifier
    if kind_hint_text and kind_hint in KIND_HINT_KINDS:
        return KIND_HINT_KINDS[kind_hint]
    return ssc.classify(title, url) if source.adapter_name == "ssc" else classify_notice(title, url)

def _notice_kind(source: GovernmentSource, notice: RecruitmentNotice, title: str) -> str:
    return _classify(source, title, notice.url, notice.kind_hint, _evidence(notice).get("kind_hint"))

async def reclassify_notices(session: AsyncSession) -> Dict[Tuple[str, str], int]:
    """Re-apply the (deterministic) classifier to every stored notice, so classifier fixes also reach notices
    that are no longer on any listing page. Returns {(old_kind, new_kind): count} for the rows it changed."""
    changed: Dict[Tuple[str, str], int] = {}
    rows = (await session.execute(select(RecruitmentNotice, GovernmentSource)
                                  .join(GovernmentSource, GovernmentSource.id == RecruitmentNotice.source_id))).all()
    for notice, source in rows:
        kind = _notice_kind(source, notice, notice.title)
        if kind != notice.notice_kind:
            changed[(notice.notice_kind, kind)] = changed.get((notice.notice_kind, kind), 0) + 1
            notice.notice_kind = kind
    await session.flush()
    return changed

def _page(url: str) -> str:
    return urldefrag(url)[0].rstrip("/")

async def _listing_pages(fetcher: PageFetcher, adapter: ModuleType, source: GovernmentSource, browser: bool,
                         main: ProbeResult) -> List[ProbeResult]:
    """The adapter's further listing pages (LISTING_URLS), each probed like the main page (policy + robots)."""
    done = {_page(u) for u in (_probe_url(source), main.final_url) if u}
    pages: List[ProbeResult] = []
    request = getattr(adapter, "LISTING_REQUEST", None)
    if request is not None:
        # The official JSON endpoint the source's single-page app reads its listing from (adapters/base.py)
        try:
            page = analyze(await fetcher.fetch_listing(request, source_policy(source)), "HTTP", source_policy(source),
                           adapter.parse_listing)
        except Exception as e:
            page = ProbeResult("UNREACHABLE", detail=f"{type(e).__name__}: {str(e)[:200]}")
        page.final_url = page.final_url or request.url
        pages.append(page)
    for url in getattr(adapter, "LISTING_URLS", []):
        if _page(url) in done:
            continue
        done.add(_page(url))
        try:
            page = await fetcher.probe(url, source_policy(source), browser, listing_parser=adapter.parse_listing)
        except Exception as e:  # one broken page must not lose the others
            page = ProbeResult("UNREACHABLE", detail=f"{type(e).__name__}: {str(e)[:200]}")
        # Error results carry no final URL; keep which page it was
        page.final_url = page.final_url or url
        pages.append(page)
    return pages

async def probe_sources(fetcher: PageFetcher, sources: List[GovernmentSource], concurrency: int = 6) -> List[ProbeResult]:
    sem = asyncio.Semaphore(concurrency)
    adapters = all_adapters()

    async def one(source: GovernmentSource) -> ProbeResult:
        async with sem:
            try:
                if source.adapter_name == "ssc":
                    return await ssc.probe(fetcher, allow_browser=getattr(fetcher, "allow_browser", False))
                browser = bool(_config(source).get("force_browser"))
                adapter = adapters.get(source.name)
                if adapter is None:
                    return await fetcher.discover(_probe_url(source), source_policy(source), browser=browser)
                result = await fetcher.discover(_probe_url(source), source_policy(source), browser=browser,
                                                listing_parser=adapter.parse_listing)
                result.listing_pages = await _listing_pages(fetcher, adapter, source, browser, result)
                return result
            except Exception as e:  # one broken site must not stop the sweep
                return ProbeResult("UNREACHABLE", detail=f"{type(e).__name__}: {str(e)[:200]}")

    return await asyncio.gather(*(one(s) for s in sources))

@dataclass
class _Seen:
    """One notice as this probe saw it: a generic link, an adapter row, or both (same URL)."""
    title: str
    url: str
    document_type: str
    found_on_url: Optional[str]
    row: Optional[ListingRow] = None
    row_key: Optional[str] = None

def row_key(row: ListingRow) -> str:
    """What tells apart rows that share the listing page as their url: the row's own stated text (a row index
    would shift as the listing changes)."""
    return " | ".join([row.title, row.advertisement_number or "",
                       row.evidence.get("published_on", ""), row.evidence.get("last_date", "")])

def _seen_notices(source: GovernmentSource, result: ProbeResult) -> Dict[str, _Seen]:
    policy = source_policy(source)
    seen: Dict[str, _Seen] = {}
    for page in [result, *result.listing_pages]:
        for row in page.listing_rows:
            # Stored notices are fetched later as official evidence: only from the source's own domain
            if not policy.allows(row.url):
                continue
            key = row_key(row) if page.final_url and _page(row.url) == _page(page.final_url) else None
            h = url_hash(row.url, key)
            if h not in seen:  # a row repeated on a later page adds nothing
                seen[h] = _Seen(row.title, row.url, _doc_type(row.url), page.final_url, row, key)
    for link in result.notices:
        seen.setdefault(url_hash(link.url), _Seen(link.title, link.url, link.document_type, result.final_url))
    return seen

_ROW_FIELDS = ("published_on", "last_date", "advertisement_number", "kind_hint")

def _apply_row(notice: RecruitmentNotice, row: ListingRow) -> None:
    """The adapter wins on every field its row states; fields it leaves unstated keep their stored value."""
    evidence = _evidence(notice)
    evidence.update(row.evidence)
    notice.title = row.title
    for name in _ROW_FIELDS:
        value = getattr(row, name)
        if value is not None:
            setattr(notice, name, value)
    if row.post_names:
        notice.post_names = json.dumps(row.post_names, ensure_ascii=False)
    notice.listing_evidence = json.dumps(evidence, ensure_ascii=False, sort_keys=True) if evidence else None

def source_status(result: ProbeResult) -> Tuple[str, Optional[str]]:
    """The source's health from everything the probe read: the configured page's own status, unless that page
    failed or listed nothing while an adapter listing page (LISTING_REQUEST / LISTING_URLS) yielded rows; then
    the source is OK and the detail names the listing page(s) the rows came from and what the main page gave."""
    if result.status == "OK":
        return result.status, result.detail
    listed = [(p.final_url, len(p.listing_rows)) for p in result.listing_pages if p.status == "OK" and p.listing_rows]
    if not listed:
        return result.status, result.detail
    rows = "; ".join(f"{n} rows from listing {url}" for url, n in listed)
    main = f"configured page {result.status}" + (f" ({result.detail})" if result.detail else "")
    return "OK", f"{rows}; {main}"

async def record_probe(session: AsyncSession, source: GovernmentSource, result: ProbeResult, now: datetime) -> SourceDiscoverySummary:
    status, detail = source_status(result)
    source.health_status = "HEALTHY" if status == "OK" else status
    source.last_verified = now
    if status == "OK":
        source.last_success = now
    else:
        source.last_failure = now
    config = _config(source)
    config.update({
        "last_probe_status": status, "http_status": result.http_status,
        "fetch_mode": result.fetch_mode, "detail": detail,
        # The configured page's own result stays visible when listing pages carried the source
        "page_status": result.status, "page_detail": result.detail,
    })
    if result.status == "OK" and result.final_url and source_policy(source).allows(result.final_url):
        config["resolved_url"] = result.final_url
    elif config.get("resolved_url") and not source_policy(source).allows(config["resolved_url"]):
        del config["resolved_url"]
    if result.listing_pages:
        config["listing_pages"] = {p.final_url: p.status for p in result.listing_pages}
    source.configuration = json.dumps(config)

    new = new_vacancy = 0
    seen = _seen_notices(source, result)
    if seen:
        known = {
            n.url_hash: n for n in (await session.execute(
                select(RecruitmentNotice).where(RecruitmentNotice.source_id == source.id,
                                                RecruitmentNotice.url_hash.in_(list(seen)))
            )).scalars()
        }
        for h, item in seen.items():
            notice = known.get(h)
            if notice is not None:
                notice.last_seen_at = now
                if item.row:
                    _apply_row(notice, item.row)
                # Classification is deterministic; re-apply so classifier fixes reach stored notices
                notice.notice_kind = _notice_kind(source, notice, item.title)
                continue
            notice = RecruitmentNotice(
                source_id=source.id, url=item.url, url_hash=h, title=item.title, listing_row_key=item.row_key,
                document_type=item.document_type, found_on_url=item.found_on_url,
                first_seen_at=now, last_seen_at=now,
                # Only sources with a vacancy parser get automatic extraction; the rest need a human
                review_status="NEW" if source.adapter_name else "MANUAL_REVIEW",
            )
            if item.row:
                _apply_row(notice, item.row)
            notice.notice_kind = _notice_kind(source, notice, item.title)
            session.add(notice)
            new += 1
            new_vacancy += notice.notice_kind in ("VACANCY", "CORRIGENDUM")
    await session.flush()
    return SourceDiscoverySummary(
        source.name, status, result.fetch_mode, result.final_url,
        len(seen), new, new_vacancy, detail,
    )

async def discover_all(
    session: AsyncSession, fetcher: PageFetcher, specs: Iterable[SourceSpec] = SOURCES, concurrency: int = 6,
) -> List[SourceDiscoverySummary]:
    sources = list((await sync_registry(session, specs)).values())
    results = await probe_sources(fetcher, sources, concurrency)
    now = datetime.now(timezone.utc)
    return [await record_probe(session, s, r, now) for s, r in zip(sources, results)]
