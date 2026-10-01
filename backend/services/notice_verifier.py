"""
Public notice check: is a pasted recruitment notice, link or PDF on an official source we track?

Deterministic only, never an LLM. Three checks, each returning its evidence:

  domain     the link's host against the registry's official domains (and gov.in / nic.in, which
             UrlPolicy already allows for official aggregators, and the banks-only bank.in); a host that
             imitates one of them
             (official domain plus another suffix, brand name inside another domain, one or two
             letters off, "gov" used outside gov.in, punycode / look-alike letters) is a LOOKALIKE
  matches    stored official notices (link, title) and vacancies (advertisement number, post name,
             organisation, closing date); every vacancy match carries the official document link and
             the page + quote its fields were parsed from
  red flags  phrases that published debunks of fake recruitment notices point to, each with its citation

The user's URL is only ever parsed as a string: it is never fetched (no SSRF). The verdict never says
"fake": the strongest negative is "not found on official sources we track".
"""
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.documents.pdf_text import extract_pages
from govcareeros.backend.models.artifact import RawDocument
from govcareeros.backend.models.notice import RecruitmentNotice
from govcareeros.backend.models.source import GovernmentSource
from govcareeros.backend.models.vacancy import Vacancy, VacancyFieldProvenance
from govcareeros.backend.parsing.upsc_advertisement import IST
from govcareeros.backend.sources.registry import SOURCES
from govcareeros.backend.sources.url_guard import GOVERNMENT_SUFFIXES, site

VERIFIER_VERSION = "notice_verifier-1"

FOUND = "FOUND_ON_OFFICIAL_SOURCE"
LOOKALIKE = "LOOKALIKE_DOMAIN"
UNOFFICIAL = "UNOFFICIAL_DOMAIN"
NOT_FOUND = "NOT_FOUND_ON_OFFICIAL_SOURCES"

MESSAGES = {
    FOUND: "Found on an official source we track.",
    LOOKALIKE: "The link imitates an official domain; it is not an official source we track. "
               "Not found on official sources we track.",
    UNOFFICIAL: "The link is not on an official government domain we track. Not found on official sources we track.",
    NOT_FOUND: "Not found on official sources we track. Check the organisation's official website before paying "
               "or sharing documents.",
}

MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 100_000

# ------------------------------------------------------------------ domains

# Second-level suffixes under which the label to the left is the registrant's name
_TWO_LEVEL = {"gov.in", "nic.in", "co.in", "org.in", "net.in", "ac.in", "res.in", "edu.in", "ind.in", "firm.in",
              "gen.in", "ernet.in", "mil.in", "co.uk", "org.uk", "com.au", "co.cm", "com.cm"}
# Labels of official hosts that name a state, a section of a site or a common word, not the organisation
_GENERIC_LABELS = {
    "www", "en", "hi", "home", "portal", "recruitment", "rectt", "rect", "careers", "career", "opportunities",
    "psc", "up", "uk", "cg", "ap", "bihar", "rajasthan", "gujarat", "beta", "webportal",
}
# Official-host labels that are ordinary English words: only an exact token counts, never a substring
_WORD_LABELS = {"rural", "mines", "steel", "coal", "labour", "tribal", "tourism", "commerce", "education",
                "panchayat", "power", "mohua", "dot"}
# Registries closed to the public: gov.in / nic.in (what UrlPolicy allows for official aggregators) and
# bank.in, which RBI made the domain for regulated banks (SBI's official careers page links sbi.bank.in)
_RESTRICTED_SUFFIXES = {**{s: "government bodies" for s in GOVERNMENT_SUFFIXES}, "bank.in": "RBI-regulated banks"}
# Common look-alike letters (Cyrillic / Greek) mapped to the Latin letter they imitate
_CONFUSABLES = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s",
    "ԁ": "d", "һ": "h", "ӏ": "l", "ո": "n", "ν": "v", "ο": "o", "α": "a", "ι": "i", "κ": "k", "τ": "t",
})

@dataclass
class DomainCheck:
    input: str
    host: str
    status: str  # OFFICIAL, LOOKALIKE, UNOFFICIAL, INVALID
    official_domain: Optional[str] = None  # the official domain it is on, or the one it imitates
    reasons: List[str] = field(default_factory=list)

def _suffix_split(host: str) -> Tuple[List[str], str]:
    labels = host.split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in _TWO_LEVEL:
        return labels[:-2], ".".join(labels[-2:])
    return labels[:-1], labels[-1] if labels else ""

def _brands(domain: str) -> Set[str]:
    names, _ = _suffix_split(domain)
    out = set()
    for label in names:
        for token in [label, *label.split("-")]:
            if len(token) >= 3 and token not in _GENERIC_LABELS:
                out.add(token)
    return out

def _levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]

def official_domains(extra: Iterable[str] = ()) -> Set[str]:
    return {site(s.official_domain) for s in SOURCES} | {site(d) for d in extra if d}

def host_of(value: str) -> Tuple[str, List[str]]:
    """Host of a URL or bare domain as typed; fact-check write-ups spell domains as 'example(dot)com'."""
    notes = []
    v = value.strip()
    v = re.sub(r"\s*[\(\[]\s*dot\s*[\)\]]\s*", ".", v, flags=re.I)
    if "://" not in v:
        v = "http://" + v.lstrip("/")
    try:
        host = (urlparse(v).hostname or "").rstrip(".").lower()
    except ValueError:
        return "", notes
    if any(ord(c) > 127 for c in host):
        notes.append("the domain contains non-Latin characters (an internationalised domain name)")
    elif any(label.startswith("xn--") for label in host.split(".")):
        try:
            decoded = host.encode("ascii").decode("idna")
            notes.append(f"punycode domain that displays as {decoded!r}")
            host = decoded
        except UnicodeError:
            notes.append("punycode domain that does not decode")
    return host, notes

def check_domain(value: str, official: Optional[Set[str]] = None) -> DomainCheck:
    official = official if official is not None else official_domains()
    host, notes = host_of(value)
    if not host or "." not in host:
        return DomainCheck(value, host, "INVALID", reasons=["no domain name in this link"])
    skeleton = unicodedata.normalize("NFKC", host).translate(_CONFUSABLES)
    if skeleton.isascii() and skeleton != host:
        notes.append(f"look-alike letters: reads as {skeleton!r}")

    if skeleton == host:
        for d in sorted(official, key=len, reverse=True):
            if host == d or host.endswith("." + d):
                return DomainCheck(value, host, "OFFICIAL", d, [f"{host} is the official domain {d} (or under it)"])
        for suffix, who in _RESTRICTED_SUFFIXES.items():
            if host.endswith("." + suffix):
                return DomainCheck(value, host, "OFFICIAL", suffix, [f"{host} is under .{suffix}, which only {who} can register"])

    reasons, imitated = list(notes), None
    for d in sorted(official, key=len, reverse=True):
        # upsc.gov.in.com, upsc.gov.in-jobs.org: the whole official domain followed by more
        if re.search(rf"(^|[.\-]){re.escape(d)}[.\-]", skeleton):
            imitated = imitated or d
            reasons.append(f"contains the official domain {d} followed by another suffix")
        elif skeleton != host and (skeleton == d or skeleton.endswith("." + d)):
            imitated = imitated or d
            reasons.append(f"look-alike letters imitating {d}")

    names, suffix = _suffix_split(skeleton)
    tokens = [t for label in names for t in re.split(r"-+", label) if t]
    if any(t in ("gov", "govt", "govin", "nic") for t in tokens) or re.search(r"(^|[.\-])gov[.\-]?in([.\-]|$)", ".".join(names)):
        reasons.append(f"uses 'gov' / 'nic' in a domain outside .gov.in and .nic.in (suffix .{suffix})")
        imitated = imitated or "gov.in"

    by_brand: Dict[str, str] = {}
    for d in official:
        for b in _brands(d):
            by_brand.setdefault(b, d)
    for token in dict.fromkeys(tokens):
        for brand, d in sorted(by_brand.items()):
            if token == brand:
                hit = f"uses the name {brand!r} of official domain {d}"
            elif len(brand) >= 4 and brand not in _WORD_LABELS and brand in token:
                hit = f"embeds the name {brand!r} of official domain {d} in {token!r}"
            elif len(brand) >= 5 and brand not in _WORD_LABELS and abs(len(token) - len(brand)) <= 2 \
                    and _levenshtein(token, brand) <= (1 if len(brand) < 8 else 2):
                hit = f"{token!r} is one or two letters away from {brand!r} of official domain {d}"
            else:
                continue
            imitated = imitated or d
            reasons.append(hit)

    if imitated:
        return DomainCheck(value, host, "LOOKALIKE", imitated, reasons)
    return DomainCheck(value, host, "UNOFFICIAL", None,
                       reasons + [f"{host} is not an official domain in our registry, nor under .gov.in / .nic.in / .bank.in"])

_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"'()\[\]]+|\b[a-z0-9\-]+(?:\.[a-z0-9\-]+)*\s*[\(\[]dot[\)\]]\s*[a-z]{2,}\b", re.I)

def urls_in(text: str) -> List[str]:
    return list(dict.fromkeys(m.group(0).rstrip(".,;:") for m in _URL_RE.finditer(text)))

# ------------------------------------------------------------------ red flags

@dataclass(frozen=True)
class RedFlagRule:
    code: str
    description: str
    pattern: re.Pattern
    citations: Tuple[str, ...]

# Only patterns that a published debunk of a fake recruitment notice points to. Each citation is the debunk.
RED_FLAG_RULES: Sequence[RedFlagRule] = (
    RedFlagRule(
        "FEE_FROM_ALL_CATEGORIES",
        "Asks applicants of all categories to pay a fee (a debunked fake notice said exactly this)",
        re.compile(r"(?:applicants|candidates)\s+of\s+all\s+categor(?:y|ies)\s+(?:should|must|shall|have\s+to|are\s+required\s+to)"
                   r"\s+(?:remit|pay|deposit)", re.I),
        ("https://news.careers360.com/fake-website-asks-rs-1645-application-fee-recruitment-in-govt-job-projects-pib",)),
    RedFlagRule(
        "FEE_FOR_SITTING_ARRANGEMENT",
        "Says a fee is mandatory for the 'sitting arrangement' (wording from a debunked fake notice)",
        re.compile(r"(?:fee|charges?)\b[^.\n]{0,80}\bsitting\s+arrangement|sitting\s+arrangement\b[^.\n]{0,80}\b(?:fee|charges?)\b", re.I),
        ("https://news.careers360.com/fake-website-asks-rs-1645-application-fee-recruitment-in-govt-job-projects-pib",)),
    RedFlagRule(
        "PAY_BY_QR_OR_UPI_ID",
        "Asks for payment by scanning a QR code or to a UPI ID (police advise against both)",
        re.compile(r"\bscan\b[^.\n]{0,40}\bQR\b|\bQR[\s-]*code\b[^.\n]{0,60}\b(?:pay|fee|amount|registration)|"
                   r"\b(?:pay|fee|amount|registration)\b[^.\n]{0,60}\bQR[\s-]*code\b|\bUPI\s*(?:ID|handle|address)\b|"
                   r"\b[\w.\-]{2,}@(?:ybl|okaxis|oksbi|okhdfcbank|okicici|paytm|upi|ibl|axl|apl)\b", re.I),
        ("https://news.careers360.com/haryana-gang-behind-fake-hssc-website-busted-main-conspirator-arrested-from-gorakhpur",
         "https://news.careers360.com/delhi-police-busts-fake-government-job-racket-kingpin-from-hyderabad-arrested")),
    RedFlagRule(
        "PROCESSING_FEE_FOR_APPOINTMENT",
        "Asks for a processing / verification / confirmation fee for an appointment",
        re.compile(r"\b(?:processing|verification|confirmation|joining)\s+(?:fees?|charges?|amount)\b", re.I),
        ("https://www.outlookmoney.com/news/got-a-government-job-appointment-letter-heres-what-pib-fact-check-says",
         "https://the420.in/raipur-recruitment-scam-fake-appointment-letters-job-fraud/")),
    RedFlagRule(
        "WHATSAPP_CONTACT",
        "Gives a WhatsApp contact or asks for documents over WhatsApp (fake appointment letters spread this way)",
        re.compile(r"whats\s*app\b[^.\n]{0,60}(?:\+?91[\s-]?)?\d{5}\s?\d{5}|(?:send|share|contact|message|apply)\b[^.\n]{0,40}\bwhats\s*app", re.I),
        ("https://the420.in/raipur-recruitment-scam-fake-appointment-letters-job-fraud/",)),
    RedFlagRule(
        "REGISTRATION_FEE",
        "Charges a 'registration fee' to apply (fake job portals collected these)",
        re.compile(r"\b(?:non[\s-]?refundable\s+)?registration\s+(?:fees?|charges?)\b", re.I),
        ("https://www.cyberyodha.org/2023/10/beware-of-scams-fake-government-job.html",
         "https://news.careers360.com/delhi-police-busts-fake-government-job-racket-kingpin-from-hyderabad-arrested")),
)

@dataclass
class RedFlag:
    code: str
    description: str
    quote: str
    citations: List[str]

def _sentence_around(text: str, start: int, end: int, limit: int = 240) -> str:
    left = max(text.rfind(".", 0, start), text.rfind("\n", 0, start)) + 1
    stops = [i for i in (text.find(".", end), text.find("\n", end)) if i != -1]
    right = min(stops) + 1 if stops else len(text)
    quote = re.sub(r"\s+", " ", text[left:right]).strip()
    return quote if len(quote) <= limit else quote[:limit - 1] + "…"

def red_flags(text: str) -> List[RedFlag]:
    out = []
    for rule in RED_FLAG_RULES:
        m = rule.pattern.search(text)
        if m:
            out.append(RedFlag(rule.code, rule.description, _sentence_around(text, m.start(), m.end()), list(rule.citations)))
    return out

# ------------------------------------------------------------------ matching stored official notices

_STOPWORDS = {"the", "and", "for", "of", "in", "to", "on", "by", "vide", "with", "from", "at", "a", "an", "no", "is",
              "are", "be", "or", "as", "under", "its", "this", "that", "regarding", "notice", "pdf", "download"}
_ADVT_RE = re.compile(
    r"\b(?:advt|advertisement|adv|cen|employment\s+notice|notification)\.?\s*(?:no|number)?\.?\s*[:.\-]?\s*"
    r"((?:[A-Z]{1,6}[\s/\-]*)?\d{1,4}\s*[/\-.]\s*(?:\d{4}|\d{2}))\b", re.I)
_DATE_RE = re.compile(r"\b(\d{1,2})[./\-](\d{1,2})[./\-](\d{4})\b")

def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower().replace("’", "'")
    return re.sub(r"[^a-z0-9/]+", " ", text).strip()

def _tokens(text: str) -> List[str]:
    return [t for t in _norm(text).replace("/", " ").split() if len(t) >= 3 and t not in _STOPWORDS]

def canonical_advt(raw: str) -> str:
    """'12/2026', '12-2026', 'Advt. No. 13-25' -> '12/2026' / '13/2025' (two-digit years are 20xx)."""
    m = re.match(r"^(?:([A-Z]{1,6})[\s/\-]*)?(\d{1,4})\s*[/\-.]\s*(\d{4}|\d{2})$", raw.strip(), re.I)
    if not m:
        return re.sub(r"\s+", "", raw).upper()
    prefix, num, year = m.groups()
    year = year if len(year) == 4 else "20" + year
    return f"{prefix.upper() + ' ' if prefix else ''}{int(num)}/{year}"

def advertisement_numbers(text: str) -> List[str]:
    return list(dict.fromkeys(canonical_advt(m.group(1)) for m in _ADVT_RE.finditer(text)))

def dates_in(text: str) -> Set[date]:
    out = set()
    for d, m, y in _DATE_RE.findall(text):
        try:
            out.add(date(int(y), int(m), int(d)))
        except ValueError:
            pass
    return out

def _url_variants(url: str) -> Set[str]:
    p = urlparse(url.strip())
    if not p.hostname:
        return set()
    host = site(p.hostname)
    rest = p.path + (f"?{p.query}" if p.query else "")
    return {f"{scheme}://{h}{rest}" for scheme in ("http", "https") for h in (host, "www." + host)}

@dataclass
class NoticeMatch:
    kind: str  # NOTICE (official link seen on a source page) or VACANCY (parsed from the official document)
    title: str
    organization: str
    source: str
    official_url: Optional[str]
    matched_on: List[str]
    advertisement_number: Optional[str] = None
    closing_date: Optional[date] = None
    page: Optional[int] = None
    quote: Optional[str] = None
    status: Optional[str] = None  # a vacancy's ACTIVE / CANCELLED, as recorded from official notices
    vacancy_id: Optional[str] = None
    notice_id: Optional[str] = None

def _source_names(sources: Iterable[GovernmentSource]) -> Dict[str, Set[str]]:
    """Every way the input may name a source's organisation: its full name and its short name (UPSC)."""
    names: Dict[str, Set[str]] = {}
    specs = {s.name: s.organization for s in SOURCES}
    for s in sources:
        org = specs.get(s.name, s.organization)
        names[s.name] = {_norm(org), _norm(s.organization), _norm(s.name.split("-")[0])}
    return names

def _names_org(text_norm: str, names: Set[str]) -> bool:
    return any(n and re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", text_norm) for n in names)

async def match_official(session: AsyncSession, text: str, urls: Sequence[str] = ()) -> List[NoticeMatch]:
    text_norm = _norm(text)
    text_tokens = set(_tokens(text))
    advts = set(advertisement_numbers(text))
    stated_dates = dates_in(text)
    variants = {v for u in urls for v in _url_variants(u)}

    sources = {s.id: s for s in (await session.execute(select(GovernmentSource))).scalars()}
    names = _source_names(sources.values())
    matches: List[NoticeMatch] = []

    # Vacancies parsed from official documents
    vacancies = (await session.execute(select(Vacancy))).scalars().all()
    for v in vacancies:
        src = sources.get(v.source_id)
        on = []
        if variants & {v.notification_url, v.source_url, v.application_url}:
            on.append("official link")
        if v.advertisement_number and canonical_advt(v.advertisement_number) in advts:
            on.append("advertisement number")
        post = _norm(v.post_name or "")
        if len(post) >= 6 and re.search(rf"(?<![a-z0-9]){re.escape(post)}(?![a-z0-9])", text_norm):
            on.append("post name")
        org_names = (names.get(src.name, set()) if src else set()) | {_norm(v.organization or "")}
        if _names_org(text_norm, org_names):
            on.append("organisation")
        closing = v.application_deadline.astimezone(IST).date() if v.application_deadline else None
        if closing and closing in stated_dates:
            on.append("closing date")
        strong = "official link" in on or ("advertisement number" in on and ("organisation" in on or "post name" in on)) \
            or ("post name" in on and "organisation" in on)
        if strong:
            matches.append(NoticeMatch("VACANCY", v.post_name, v.organization, src.name if src else "", v.notification_url,
                                       on, v.advertisement_number, closing, status=v.status, vacancy_id=str(v.id)))

    # Official document page + quote for each vacancy match, from the parse's own provenance
    by_vacancy = {m.vacancy_id: m for m in matches}
    if by_vacancy:
        rows = (await session.execute(
            select(VacancyFieldProvenance, RawDocument.url)
            .join(RawDocument, RawDocument.id == VacancyFieldProvenance.raw_document_id)
            .where(VacancyFieldProvenance.vacancy_id.in_([v.id for v in vacancies if str(v.id) in by_vacancy]))
        )).all()
        preference = {"post_name": 0, "advertisement_number": 1}
        rows = sorted(rows, key=lambda r: preference.get(r[0].field_name, 9))
        for prov, doc_url in rows:
            m = by_vacancy[str(prov.vacancy_id)]
            if m.quote is None and prov.field_name in preference:
                m.page, m.quote = prov.page, prov.excerpt
            m.official_url = m.official_url or doc_url

    # Notice links seen on official source pages
    notices = (await session.execute(select(RecruitmentNotice))).scalars().all()
    for n in notices:
        src = sources.get(n.source_id)
        on = []
        if n.url in variants:
            on.append("official link")
        title_tokens = _tokens(n.title)
        if len(title_tokens) >= 5:
            covered = sum(t in text_tokens for t in title_tokens) / len(title_tokens)
            if covered >= 0.9:
                on.append("title")
            elif len(text_norm) <= 3 * len(_norm(n.title)) and \
                    SequenceMatcher(None, _norm(n.title), text_norm).ratio() >= 0.85:
                on.append("title")
        title_advts = set(advertisement_numbers(n.title))
        if title_advts & advts and src and _names_org(text_norm, names.get(src.name, set())):
            on.append("advertisement number")
        if on:
            matches.append(NoticeMatch("NOTICE", n.title, src.organization if src else "", src.name if src else "",
                                       n.url, on, notice_id=str(n.id)))

    def rank(m: NoticeMatch):
        return (-("official link" in m.matched_on), -len(m.matched_on), m.kind != "VACANCY", m.title)
    return sorted(matches, key=rank)

# ------------------------------------------------------------------ verdict

@dataclass
class VerificationResult:
    verdict: str
    message: str
    verifier_version: str
    url: Optional[DomainCheck]
    links_in_text: List[DomainCheck]
    matches: List[NoticeMatch]
    red_flags: List[RedFlag]
    advertisement_numbers: List[str]
    text_chars: int

def pdf_text(body: bytes) -> str:
    # Text layer only: OCR of an anonymous upload would let one request hold a worker for minutes
    return "\n".join(p.text for p in extract_pages(body, ocr=False))

async def verify(session: AsyncSession, text: Optional[str] = None, url: Optional[str] = None,
                 pdf: Optional[bytes] = None) -> VerificationResult:
    text = (text or "")[:MAX_TEXT_CHARS]
    if pdf:
        text = (text + "\n" + pdf_text(pdf)).strip()
    sources = (await session.execute(select(GovernmentSource.official_domain))).scalars().all()
    official = official_domains(sources)

    url_check = check_domain(url, official) if url and url.strip() else None
    links = [check_domain(u, official) for u in urls_in(text)]
    # Exact links are looked up whatever their domain: a stored notice link was seen on an official source page
    matches = await match_official(session, text, [c.input for c in [url_check, *links] if c])
    flags = red_flags(text)

    if any("official link" in m.matched_on for m in matches):
        verdict = FOUND
    elif (url_check and url_check.status == "LOOKALIKE") or any(c.status == "LOOKALIKE" for c in links):
        verdict = LOOKALIKE
    elif url_check and url_check.status in ("UNOFFICIAL", "INVALID"):
        verdict = UNOFFICIAL
    elif matches or (url_check and url_check.status == "OFFICIAL"):
        verdict = FOUND
    else:
        verdict = NOT_FOUND

    message = MESSAGES[verdict]
    if verdict == FOUND and not matches:
        message = (f"The link is on the official domain {url_check.official_domain}; we have not indexed this "
                   "notice yet, so open it there to confirm.")
    return VerificationResult(verdict, message, VERIFIER_VERSION, url_check, links, matches, flags,
                              advertisement_numbers(text), len(text))
