"""
How a post is filled, read deterministically from the notice's own words.

  DEPUTATION  "on (a) deputation basis", "by deputation", "officers holding analogous posts",
              "filled (exclusively) by officers working under the Central/State Government"
  CONTRACT    "on (a) contract/contractual basis", "contractual engagement/appointment"
  WALK_IN     "walk-in interview/selection" (walk-ins are how contractual posts are filled, so
              WALK_IN absorbs CONTRACT)
  DIRECT      "direct recruitment"
  UNKNOWN     nothing stated, or modes that exclude each other (e.g. "deputation failing which direct
              recruitment"): such a post is not deputation-only, so nothing is ruled out on that basis

The DoPT phrase "deputation (including short-term contract)" is deputation, not a contract.
For DEPUTATION the text also says who may apply: `open_to` is ("CENTRAL",) when it limits the post to
Central Government officers or declares State Government employees ineligible, ("CENTRAL", "STATE")
when it names both, and () when it says neither. Nothing is inferred beyond the matched sentences.
"""
import bisect
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from govcareeros.backend.documents.pdf_text import PageText

MODES = ("DIRECT", "DEPUTATION", "CONTRACT", "WALK_IN", "UNKNOWN")

_SHORT_TERM_CONTRACT_RE = re.compile(r"\(\s*including\s+short[\s-]*term\s+contract\s*\)", re.I)
_SIGNALS: Dict[str, re.Pattern] = {
    # OCR joins words ("ona deputation basis"), so spaces between the small words are optional
    "DEPUTATION": re.compile(
        r"\bon\s*(?:a\s*)?deputation\b(?!\s+and\s+foreign)|\bdeputation\s+basis|\bby\s+(?:way\s+of\s+)?deputation\b|"
        r"\bholding\s+(?:an\s+)?analogous\s+posts?|\bofficers\s+holding\s+analogous|"
        r"\bfilled\s+(?:up\s+)?(?:exclusively\s+|only\s+)?by\s+(?:the\s+)?(?:officers|employees)\s+(?:currently\s+)?"
        r"(?:working\s+)?(?:under|of|in)\s+(?:the\s+)?(?:Central|State)", re.I),
    "CONTRACT": re.compile(r"\bon\s*(?:a\s+)?contract(?:ual)?\s+basis|\bcontractual\s+(?:engagement|appointment)", re.I),
    "WALK_IN": re.compile(r"\bwalk[\s-]*in[\s-]*(?:interview|selection)", re.I),
    "DIRECT": re.compile(r"\bdirect\s+recruitment\b", re.I),
}
_CENTRAL_ONLY_RE = re.compile(
    r"State\s+Government\s+employees?.{0,200}?\bnot\s+eligible|"
    r"(?:exclusively|only)\s+by\s+(?:the\s+)?officers\s+.{0,40}?Central\s+Government", re.I | re.S)
_CENTRAL_AND_STATE_RE = re.compile(
    r"Central\s*(?:/|or|and)\s*State\s+Governments?|State\s*(?:/|or|and)\s*Central\s+Governments?|"
    r"State\s+Government\s+(?:employees|officers|servants)\s+(?:are\s+)?(?:also\s+)?eligible", re.I)

@dataclass
class ModeDetection:
    mode: str
    start: int  # offset of the deciding phrase in the searched text
    end: int
    excerpt: str
    open_to: Tuple[str, ...] = ()
    signals: Dict[str, str] = field(default_factory=dict)  # mode -> first phrase found for it
    page: Optional[int] = None

def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()

def _sentence(text: str, start: int, end: int, width: int = 160) -> str:
    return _clean(text[max(0, start - width):end + width])[:400]

def detect_recruitment_mode(text: str) -> Optional[ModeDetection]:
    """None when the text states no mode at all."""
    # Same length replacement keeps offsets valid for provenance
    searched = _SHORT_TERM_CONTRACT_RE.sub(lambda m: " " * len(m.group(0)), text)
    found: Dict[str, re.Match] = {}
    for mode, rx in _SIGNALS.items():
        m = rx.search(searched)
        if m:
            found[mode] = m
    if not found:
        return None
    modes = set(found)
    if modes == {"WALK_IN", "CONTRACT"}:
        modes = {"WALK_IN"}
    mode = modes.pop() if len(modes) == 1 else "UNKNOWN"
    first = min(found.values(), key=lambda m: m.start())
    deciding = found.get(mode, first)
    open_to: Tuple[str, ...] = ()
    if mode == "DEPUTATION":
        if _CENTRAL_ONLY_RE.search(text):
            open_to = ("CENTRAL",)
        elif _CENTRAL_AND_STATE_RE.search(text):
            open_to = ("CENTRAL", "STATE")
    return ModeDetection(mode, deciding.start(), deciding.end(), _sentence(text, deciding.start(), deciding.end()), open_to,
                         {k: _clean(m.group(0)) for k, m in found.items()})

def detect_in_pages(pages: Sequence[PageText]) -> Optional[ModeDetection]:
    """Detection over a whole document, with the 1-based page of the deciding phrase."""
    starts: List[int] = []
    parts, offset = [], 0
    for p in pages:
        starts.append(offset)
        parts.append(p.text)
        offset += len(p.text) + 1
    detection = detect_recruitment_mode("\n".join(parts))
    if detection and pages:
        detection.page = pages[bisect.bisect_right(starts, detection.start) - 1].page_number
    return detection

def govt_service_requirement(detection: Optional[ModeDetection]) -> Optional[dict]:
    """The GOVT_SERVICE requirement value a deputation-only post carries, or None for any other mode."""
    if detection is None or detection.mode != "DEPUTATION":
        return None
    return {"mode": "DEPUTATION", "open_to": list(detection.open_to)}
