"""
Normalised words of a notice title, shared by the public notice check (backend/services/notice_verifier.py) and the
title index it searches (recruitment_notice_title_tokens, kept up to date by backend/models/notice.py).
"""
import re
import unicodedata
from collections import Counter
from typing import Dict, List

STOPWORDS = {"the", "and", "for", "of", "in", "to", "on", "by", "vide", "with", "from", "at", "a", "an", "no", "is",
             "are", "be", "or", "as", "under", "its", "this", "that", "regarding", "notice", "pdf", "download"}
# Titles with fewer words are never matched by their wording (too little to tell notices apart)
MIN_TITLE_TOKENS = 5

def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower().replace("’", "'")
    return re.sub(r"[^a-z0-9/]+", " ", text).strip()

def tokens(text: str) -> List[str]:
    return [t for t in normalise(text).replace("/", " ").split() if len(t) >= 3 and t not in STOPWORDS]

def token_counts(title: str) -> Dict[str, int]:
    """Each distinct word of the title with how often it occurs; empty below MIN_TITLE_TOKENS words."""
    words = tokens(title)
    return dict(Counter(words)) if len(words) >= MIN_TITLE_TOKENS else {}
