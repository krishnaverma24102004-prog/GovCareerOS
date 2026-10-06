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
        r"list\s+of\s+(?:\d+\s+)?(?:selected|eligible|candidates)|final\s+selection|"
        # Candidature decisions on an existing recruitment (JKPSC / HPSC / PPSC public notices)
        r"rejection\s+(?:of\s+(?:the\s+)?candidature|notice|list)|provisionally\s+reject|ineligibility\s+of\s+candidates|"
        r"deficienc(?:y|ies)|discrepanc(?:y|ies)|qualified\s+candidates|(?:sports|pwd)\s+(?:category\s+)?candidates|"
        r"person\s+with\s+disability\s+candidates|inspection\s+of\s+(?:the\s+)?answer\s+(?:booklets?|sheets?|scripts?)", re.I)),
    ("RULES", re.compile(r"recruitment\s+rules|\bRRs?\b|comments\s+on\s+recruitment|service\s+rules|"
                         r"inviting\s+comments(?:\s*/\s*observations?)?\s+of\s+stakeholders?", re.I)),
    ("CORRIGENDUM", re.compile(r"corrigendum|addendum|clarification|extension\s+(?:of|in)\s+(?:the\s+)?(?:last\s+)?dates?|date\s+extended|cancell?ation|cancell?ed|postpone|deferment", re.I)),
    # Process notices about an existing recruitment (interview/PMT schedules, exam scheme and syllabus), not new vacancies
    ("EXAM_SCHEDULE", re.compile(
        r"schedule\s+of\s+(?:the\s+)?(?:interviews?|written|physical|pmt|pet|pst|exam(?:ination)?s?|tests?|document|dv|skill)|"
        r"scheme\s+(?:and|&)\s+(?:pattern|syllabus)|\bsyllabus\b|time[\s-]?slots?\b|re-?schedul|tentative\s+schedule|"
        r"date\s+of\s+(?:the\s+)?(?:competitive\s+)?exam(?:ination)?|exam(?:ination)?\s+date|scrutiny\s+of\s+documents|"
        r"date\s*sheet|for\s+(?:the\s+)?skill\s+test", re.I)),
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
