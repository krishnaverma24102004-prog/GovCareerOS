"""
Deterministic keyword classification of a notice link into VACANCY / CORRIGENDUM /
RESULT / RULES / OTHER. Order matters: a "Result for recruitment of ..." link is a
RESULT even though it mentions recruitment.
"""
import re
from urllib.parse import unquote, urlparse

_RULES = [
    ("RESULT", re.compile(
        r"\bresults?\b|selected\s+candidates|provisionally\s+selected|short[\s-]?listed|shortlist|merit\s+list|"
        r"nominal\s+roll|marks|answer\s+key|admit\s+card|e-?admit|call\s+letter|interview\s+schedule|"
        r"list\s+of\s+(?:\d+\s+)?(?:selected|eligible|candidates)|final\s+selection", re.I)),
    ("RULES", re.compile(r"recruitment\s+rules|\bRRs?\b|comments\s+on\s+recruitment|service\s+rules", re.I)),
    ("CORRIGENDUM", re.compile(r"corrigendum|addendum|extension\s+of\s+(?:last\s+)?date|date\s+extended|cancell?ation|cancell?ed|postpone", re.I)),
    ("VACANCY", re.compile(
        r"vacanc|advertis|\badvt\b|recruitment\s+(?:of|for|to|through)|filling\s*up|deputation|engagement\s+of|"
        r"walk[\s-]?in|applications?\s+(?:are\s+)?invited|inviting\s+(?:online\s+)?applications|"
        r"notification\s+for\s+recruitment|online\s+application|post\s+of", re.I)),
]

def classify_notice(title: str, url: str) -> str:
    # Link text like "Download" / "VIEW" carries nothing; the URL path (folder + file name,
    # e.g. /files/vacancy_attachment/Fillingupof1post.pdf) often does
    path = re.sub(r"[/_\-.]+", " ", unquote(urlparse(url).path))
    text = f"{title} {path}"
    for kind, pattern in _RULES:
        if pattern.search(text):
            return kind
    return "OTHER"
