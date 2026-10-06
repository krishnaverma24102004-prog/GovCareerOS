"""
Deputation vacancy circulars, read deterministically. Every term keeps its page and the exact
(whitespace-collapsed, NFKC-folded) words it was read from; a term the circular does not state stays None.

Circular-level terms
  mode          DEPUTATION / DEPUTATION_STC ("deputation (including short-term contract)") / ABSORPTION /
                COMPOSITE ("composite method") / RE_EMPLOYMENT (Armed Forces personnel), from the "Mode of
                Recruitment" field, a "Deputation/absorption:" eligibility head, or the "posts on deputation" sentence
  max_age       "maximum age limit ... 56 years / not exceeding fifty-four years", with what it is counted on
                ("closing date" / "last date" of receipt of applications) when the sentence says so
  tenure        initial period, extension and the "shall ordinarily not exceed N years" ceiling
  closing       when applications close: a stated date (DATE) or "within N days from the date of
                publication ... in the Employment News" (EMPLOYMENT_NEWS_DAYS, the date then depends on the issue)
  documents     what the forwarding authority must send: APARs of the last five years, vigilance clearance,
                integrity certificate, major/minor penalty statement, cadre clearance, NOC, proper channel
  feeder_bar    "departmental officers in the feeder category ... in the direct line of promotion shall not be eligible"

Posts (one block per post: "Name of post:", "RECRUITMENT RULES FOR THE POST OF", or the subject of a
single-post circular) carry the post's own pay level, mode, "holding analogous post on regular basis" clause,
"N years' regular service in Level X" clauses, max age and tenure notes. A clause the layout breaks apart
(e.g. a level number pushed to the next column) keeps levels=() rather than a guess.

to_json / from_json turn a parsed circular into the plain JSON stored on a DeputationCircular row and back
(from_json(to_json(c)) == c); PARSER_VERSION is stored with it so a parser change can re-read stored PDFs.
"""
import re
import unicodedata
from dataclasses import dataclass, field, fields
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

from govcareeros.backend.documents.pdf_text import PageText
from govcareeros.backend.parsing.upsc_advertisement import words_to_number

PARSER_VERSION = "deputation_circular/1"
MODES = ("DEPUTATION", "DEPUTATION_STC", "ABSORPTION", "COMPOSITE", "RE_EMPLOYMENT")
DOCUMENTS = ("APAR_LAST_5_YEARS", "VIGILANCE_CLEARANCE", "INTEGRITY_CERTIFICATE", "PENALTY_STATEMENT",
             "CADRE_CLEARANCE", "NOC", "PROPER_CHANNEL")

@dataclass
class Quoted:
    value: object
    page: int
    quote: str

@dataclass
class ServiceClause:
    years: int
    levels: Tuple[int, ...]   # pay-matrix levels the service must be in; () when the text does not give them clearly
    regular: bool             # "regular service" / "on a regular basis"
    combined: bool            # service in the listed levels may be taken together
    page: int
    quote: str

@dataclass
class DeputationPost:
    name: Quoted
    pay_level: Optional[Quoted] = None
    mode: Optional[Quoted] = None                 # value: tuple of MODES
    analogous: Optional[Quoted] = None            # "holding analogous post on regular basis" (never auto-decided)
    service: List[ServiceClause] = field(default_factory=list)
    max_age: Optional[Quoted] = None
    tenure_max_years: Optional[Quoted] = None

@dataclass
class DeputationCircular:
    posts: List[DeputationPost] = field(default_factory=list)
    mode: Optional[Quoted] = None
    max_age: Optional[Quoted] = None              # value: {"years": 56, "on": "CLOSING_DATE" | None}
    tenure_initial_years: Optional[Quoted] = None
    tenure_extension_years: Optional[Quoted] = None
    tenure_max_years: Optional[Quoted] = None
    closing: Optional[Quoted] = None              # value: {"kind": "DATE", "date": date} | {"kind": "EMPLOYMENT_NEWS_DAYS", "days": 45}
    documents: Dict[str, Quoted] = field(default_factory=dict)
    feeder_bar: Optional[Quoted] = None
    asks_last_deputation: Optional[Quoted] = None  # the pro forma asks the date of return from the last deputation

# ---------- text with page offsets ----------

def _norm(text: str) -> str:
    # NFKC folds ligatures ("ﬁfty") and full-width forms; quotes stay the document's words
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()

class _Doc:
    def __init__(self, pages: Sequence[PageText]):
        self.starts: List[int] = []
        self.numbers: List[int] = []
        parts, offset = [], 0
        for p in pages:
            t = _norm(p.text)
            self.starts.append(offset)
            self.numbers.append(p.page_number)
            parts.append(t)
            offset += len(t) + 1
        self.text = " ".join(parts)

    def page(self, pos: int) -> int:
        i = 0
        for k, s in enumerate(self.starts):
            if s <= pos:
                i = k
        return self.numbers[i] if self.numbers else 1

    def quoted(self, value, m: re.Match, start: Optional[int] = None, end: Optional[int] = None) -> Quoted:
        s, e = (m.start() if start is None else start), (m.end() if end is None else end)
        return Quoted(value, self.page(s), self.text[s:e].strip())

_NUM = r"(\d{1,2}|(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty|fifty|sixty)(?:[\s-](?:one|two|three|four|five|six|seven|eight|nine))?)"

def _number(word: str) -> Optional[int]:
    return words_to_number(word.replace("-", " "))

def _dmy(text: str) -> Optional[date]:
    m = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", text.strip())
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None

# ---------- mode ----------

_MODE_FIELD_RE = re.compile(
    r"Mode\s+of\s+Recruitment\s*:\s*(?:1\.\s*)?(?:By\s+)?"
    r"((?:deputation\s*\(\s*including\s+short[\s-]*term\s+contract\s*\)|deputation\s*/\s*absorption|composite\s+method|"
    r"absorption|deputation)(?:\s+basis)?(?:\s*\.?\s*2\.\s*For\s+Armed\s+Forces\s+Personnel\s*:\s*Deputation\s*/\s*re-employment)?)",
    re.I)
_MODE_HEAD_RE = re.compile(r"\bDeputation\s*/\s*absorption\s*:", re.I)
_MODE_SENTENCE_RE = re.compile(
    r"posts?\s+(?:in\s+\S+\s+)?on\s+deputation\s*(?:\(\s*including\s+short[\s-]*term\s+contract\s*\)|basis)|"
    r"\bon\s+deputation\s+basis", re.I)
_ARMED_FORCES_RE = re.compile(r"Deputation\s*/\s*re-employment\s+(?:for|\(for)\s+Armed\s+Forces\s+Personnel", re.I)

def classify_mode(phrase: str) -> Tuple[str, ...]:
    p = phrase.lower()
    modes = []
    if "composite" in p:
        modes.append("COMPOSITE")
    if re.search(r"short[\s-]*term\s+contract", p):
        modes.append("DEPUTATION_STC")
    elif "deputation" in p:
        modes.append("DEPUTATION")
    if "absorption" in p:
        modes.append("ABSORPTION")
    if "re-employment" in p:
        modes.append("RE_EMPLOYMENT")
    return tuple(modes)

def _mode(doc: _Doc, start: int, end: int) -> Optional[Quoted]:
    for rx in (_MODE_FIELD_RE, _MODE_HEAD_RE, _MODE_SENTENCE_RE):
        m = rx.search(doc.text, start, end)
        if m:
            modes = classify_mode(m.group(0))
            if "RE_EMPLOYMENT" not in modes and _ARMED_FORCES_RE.search(doc.text, start, end):
                modes += ("RE_EMPLOYMENT",)
            return doc.quoted(modes, m)
    return None

# ---------- age, tenure, closing ----------

_AGE_RE = re.compile(r"maximum\s+age[\s-]*limit\b[^.]{0,120}?\b" + _NUM + r"\s+years\b", re.I)
_AGE_BASIS_RE = re.compile(
    r"^[’'\s]*(?:as\s+on|on)\s+the\s+(closing|last)\s+date(?:\s+of\s+(?:receipt\s+of\s+)?applications?)?", re.I)

def _max_age(doc: _Doc, start: int, end: int) -> Optional[Quoted]:
    m = _AGE_RE.search(doc.text, start, end)
    if not m:
        return None
    years = _number(m.group(1))
    if years is None or not 18 <= years <= 70:
        return None
    basis = _AGE_BASIS_RE.match(doc.text[m.end():m.end() + 140])
    on = "CLOSING_DATE" if basis else None
    return doc.quoted({"years": years, "on": on}, m, end=m.end() + (basis.end() if basis else 0))

_TENURE_INITIAL_RE = re.compile(
    r"(?:period\s+of\s+" + _NUM + r"\s+years\s+initially|initial\s+period\s+of\s+" + _NUM + r"\s+years)", re.I)
_TENURE_EXTENSION_RE = re.compile(r"extended\s+for\s+a\s+further\s+period\s+of\s+" + _NUM + r"\s+years", re.I)
_TENURE_MAX_RE = re.compile(
    r"(?:shall\s+ordinarily\s+not\s+(?:to\s+)?exceed\s+" + _NUM + r"\s+years|subject\s+to\s+a\s+maximum\s+period\s+of\s+" + _NUM + r"\s+years)",
    re.I)

def _years_term(doc: _Doc, rx: re.Pattern, start: int, end: int) -> Optional[Quoted]:
    m = rx.search(doc.text, start, end)
    if not m:
        return None
    years = _number(next(g for g in m.groups() if g))
    return doc.quoted(years, m) if years else None

_DATE = r"(\d{1,2}[./-]\d{1,2}[./-]\d{4})"
_CLOSING_EN_RE = re.compile(
    r"within\s+(?:a\s+period\s+of\s+)?" + _NUM + r"\s+days\s+from\s+the\s+date\s+of\s+(?:publication|issue)\s+of\s+"
    r"[^.]{0,80}?Employment\s+News", re.I)
_CLOSING_DATE_RES = (
    re.compile(r"closing\s+date\s+of\s+receipt\s+of\s+applications?\s+i\.?\s?e\.?\s*" + _DATE, re.I),
    re.compile(r"last\s+date\s+of\s+receipt\s+of\s+applications?[^:.]{0,40}:\s*" + _DATE, re.I),
    re.compile(r"submitted\s+w\.?e\.?f\.?\s*" + _DATE + r"\s+to\s+" + _DATE, re.I),
)

def _closing(doc: _Doc) -> Optional[Quoted]:
    m = _CLOSING_EN_RE.search(doc.text)
    if m:
        days = _number(m.group(1))
        if days:
            return doc.quoted({"kind": "EMPLOYMENT_NEWS_DAYS", "days": days}, m)
    for rx in _CLOSING_DATE_RES:
        m = rx.search(doc.text)
        if m:
            when = _dmy(m.groups()[-1])
            if when:
                return doc.quoted({"kind": "DATE", "date": when}, m)
    return None

# ---------- documents to be forwarded ----------

_DOCUMENT_RES: Dict[str, re.Pattern] = {
    "APAR_LAST_5_YEARS": re.compile(r"\b(?:ACR|APAR)s?\b[^.;]{0,60}?\blast\s+(?:5|five)\s+years", re.I),
    "VIGILANCE_CLEARANCE": re.compile(r"vigilance\s+clearance(?:\s+certificate)?|no\s+vigilance\s*(?:/|or)\s*disciplinary\s+case", re.I),
    "INTEGRITY_CERTIFICATE": re.compile(r"integrity\s+certificate|integrity\s+is\s+certified", re.I),
    "PENALTY_STATEMENT": re.compile(r"major\s*(?:/|and|or)\s*minor\s+penalt(?:y|ies)[^.;]{0,60}?(?:10|ten)\s+years", re.I),
    "CADRE_CLEARANCE": re.compile(r"cadre\s+clearance", re.I),
    "NOC": re.compile(r"No\s+Objection\s+Certificate|\bNOC\b|has\s+no\s+objection", re.I),
    "PROPER_CHANNEL": re.compile(
        r"(?:through|without)\s+(?:the\s+)?proper\s+channel|forwarded\s+by\s+the\s+(?:concerned\s+)?Cadre\s+Controlling\s+Authority|"
        r"submitted\s+by\s+the\s+applicant\s+to\s+his/her\s+Cadre\s+Controlling\s+Authority", re.I),
}
_FEEDER_RE = re.compile(r"departmental\s+officers?\s+in\s+(?:the\s+)?feeder\s+category[^.]{0,120}?not\s+be\s+eligible[^.]*", re.I)
_LAST_DEPUTATION_RE = re.compile(r"date\s+of\s+return\s+from\s+the\s+last\s+deputation", re.I)

# ---------- posts ----------

_POST_ANCHORS = (
    # NATGRID-style tables: "(a) Name of post: Deputy Director (Relationship Management)"
    re.compile(r"Name\s+of\s+post\s*:\s*([A-Z][^():;]{2,80}?(?:\([^)]{2,60}\)|(?=\s*(?:\.|\(b\)|\d|Officers\b))))"),
    # Abstract of recruitment rules: "RECRUITMENT RULES FOR THE POST OF SENIOR ACCOUNTS OFFICER Scale of Pay"
    re.compile(r"RECRUITMENT\s+RULES\s+FOR\s+THE\s+POST\s+OF\s+(.{3,90}?)\s+Scale\s+of\s+Pay", re.I),
)
_SUBJECT_POST_RE = re.compile(r"Filling\s+up\s+of\s+(?:\d+\s+)?posts?\s+of\s+(.{3,80}?)\s+on\s+deputation", re.I)

# OCR splits two-digit levels ("Level-1 1 in the pay matrix")
_LEVEL = r"(\d\s\d(?=\s+in\s+the\s+pay)|\d{1,2})"
_PAY_LEVEL_RES = (
    re.compile(r"(?:Pay\s+Scale|Scale\s+of\s+Pay)\s*:\s*Level[\s_-]*" + _LEVEL, re.I),
    re.compile(r"\bLevel[\s_-]*" + _LEVEL + r"\s*\(\s*Rs\.?[^)]{0,30}\)\s*in\s+the\s+pay\s+matrix", re.I),
)
_ANALOGOUS_RE = re.compile(
    r"holding\s+(?:an\s+|the\s+)?analogous\s+posts?\s+on\s+(?:a\s+)?regular\s+basis(?:\s+in\s+the\s+parent\s+cadre(?:\s+or\s+department)?)?",
    re.I)
_HOLDING_POST_RE = re.compile(r"holding\s+the\s+post\s+of\s+.{3,80}?\s+on\s+regular\s+basis", re.I)
_SERVICE_RE = re.compile(r"\b" + _NUM + r"\s+years?['’]?\s+(?:of\s+)?(regular\s+)?service\b", re.I)
_CLAUSE_END_RE = re.compile(r";|\bOR\b|\bAND\b|\(b\)\s+possessing|\bNote\b")
_LEVEL_RE = re.compile(r"\blevel[\s_-]*(\d{1,2})(?!\d)", re.I)
_COMBINED_RE = re.compile(r"combined|taken\s+together", re.I)
_QUOTE_MAX = 240

def _service_clauses(doc: _Doc, start: int, end: int) -> List[ServiceClause]:
    out = []
    for m in _SERVICE_RE.finditer(doc.text, start, end):
        years = _number(m.group(1))
        stop = _CLAUSE_END_RE.search(doc.text, m.end(), min(end, m.end() + 260))
        clause_end = stop.start() if stop else min(end, m.end() + 260)
        tail = doc.text[m.end():clause_end]
        # The service must be "in" a level ("regular service in Pay Level-11", "in posts in level-10"); a
        # clause the layout broke apart gives no level rather than a stray number from the next column
        named = _LEVEL_RE.findall(tail) if re.search(r"\bin\b[^.]{0,120}?\blevel", tail, re.I) else []
        levels = tuple(dict.fromkeys(int(x) for x in named))
        if years is None:
            continue
        regular = bool(m.group(2)) or bool(re.search(r"on\s+a\s+regular\s+basis", tail, re.I))
        quote_start = doc.text.rfind("with", max(start, m.start() - 20), m.start())
        s = quote_start if quote_start >= 0 else m.start()
        out.append(ServiceClause(years, levels, regular, bool(_COMBINED_RE.search(tail)), doc.page(s),
                                 doc.text[s:min(clause_end, s + _QUOTE_MAX)].strip()))
    return out

def _post_blocks(doc: _Doc) -> List[Tuple[str, int, int, int]]:
    """(name, name offset, block start, block end) per post."""
    for rx in _POST_ANCHORS:
        hits = list(rx.finditer(doc.text))
        if hits:
            out = []
            for i, m in enumerate(hits):
                end = hits[i + 1].start() if i + 1 < len(hits) else len(doc.text)
                out.append((m.group(1).strip(" .,"), m.start(1), m.start(), end))
            return out
    m = _SUBJECT_POST_RE.search(doc.text)
    if m:
        return [(m.group(1).strip(" .,"), m.start(1), 0, len(doc.text))]
    return []

def _post(doc: _Doc, name: str, name_at: int, start: int, end: int) -> DeputationPost:
    post = DeputationPost(Quoted(name, doc.page(name_at), name))
    first_clause = min([m.start() for m in _SERVICE_RE.finditer(doc.text, start, end)] + [end])
    for i, rx in enumerate(_PAY_LEVEL_RES):
        # The unlabelled "Level N (Rs. ...) in the pay matrix" counts only before the eligibility clauses,
        # where the feeder grade's level is named
        m = rx.search(doc.text, start, end if i == 0 else first_clause)
        if m:
            post.pay_level = doc.quoted(int(m.group(1).replace(" ", "")), m)
            break
    post.mode = _mode(doc, start, end)
    a = _ANALOGOUS_RE.search(doc.text, start, end) or _HOLDING_POST_RE.search(doc.text, start, end)
    if a:
        post.analogous = doc.quoted(True, a)
    post.service = _service_clauses(doc, start, end)
    post.max_age = _max_age(doc, start, end)
    post.tenure_max_years = _years_term(doc, _TENURE_MAX_RE, start, end)
    return post

def parse_circular(pages: Sequence[PageText]) -> DeputationCircular:
    doc = _Doc(pages)
    n = len(doc.text)
    out = DeputationCircular(
        mode=_mode(doc, 0, n),
        max_age=_max_age(doc, 0, n),
        tenure_initial_years=_years_term(doc, _TENURE_INITIAL_RE, 0, n),
        tenure_extension_years=_years_term(doc, _TENURE_EXTENSION_RE, 0, n),
        tenure_max_years=_years_term(doc, _TENURE_MAX_RE, 0, n),
        closing=_closing(doc),
    )
    for code, rx in _DOCUMENT_RES.items():
        m = rx.search(doc.text)
        if m:
            out.documents[code] = doc.quoted(code, m)
    m = _FEEDER_RE.search(doc.text)
    out.feeder_bar = doc.quoted(True, m) if m else None
    m = _LAST_DEPUTATION_RE.search(doc.text)
    out.asks_last_deputation = doc.quoted(True, m) if m else None
    out.posts = [_post(doc, *block) for block in _post_blocks(doc)]
    return out

# ---------- stored form ----------

def _plain(v):
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, (tuple, list)):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    return v

def _quoted_out(q: Optional[Quoted]) -> Optional[dict]:
    return None if q is None else {"value": _plain(q.value), "page": q.page, "quote": q.quote}

def _quoted_in(d: Optional[dict], name: str) -> Optional[Quoted]:
    if d is None:
        return None
    value = d["value"]
    if name == "mode":
        value = tuple(value)
    elif name == "closing" and value.get("kind") == "DATE":
        value = {**value, "date": date.fromisoformat(value["date"])}
    return Quoted(value, d["page"], d["quote"])

def to_json(c: DeputationCircular) -> dict:
    out: dict = {}
    for f in fields(c):
        v = getattr(c, f.name)
        if f.name == "posts":
            out["posts"] = [{
                **{pf.name: _quoted_out(getattr(p, pf.name)) for pf in fields(p) if pf.name != "service"},
                "service": [{"years": s.years, "levels": list(s.levels), "regular": s.regular, "combined": s.combined,
                             "page": s.page, "quote": s.quote} for s in p.service],
            } for p in v]
        elif f.name == "documents":
            out["documents"] = {k: _quoted_out(q) for k, q in v.items()}
        else:
            out[f.name] = _quoted_out(v)
    return out

def from_json(d: dict) -> DeputationCircular:
    c = DeputationCircular(**{f.name: _quoted_in(d.get(f.name), f.name) for f in fields(DeputationCircular)
                              if f.name not in ("posts", "documents")})
    c.documents = {k: _quoted_in(q, k) for k, q in d.get("documents", {}).items()}
    for p in d.get("posts", []):
        post = DeputationPost(**{pf.name: _quoted_in(p.get(pf.name), pf.name) for pf in fields(DeputationPost) if pf.name != "service"})
        post.service = [ServiceClause(s["years"], tuple(s["levels"]), s["regular"], s["combined"], s["page"], s["quote"])
                        for s in p.get("service", [])]
        c.posts.append(post)
    return c
