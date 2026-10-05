"""
Shared contract between certificate-requirement extraction (what a notice asks for) and the
certificate-validity rules (does the certificate a candidate holds satisfy it).

Every requirement keeps the page and quote it was read from; nothing here is inferred.
Real rulings these shapes were checked against: tests/fixtures/certificate_cases.json.
"""
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional

class CertificateKind(str, Enum):
    OBC_NCL = "OBC_NCL"        # OBC non-creamy layer
    EWS = "EWS"                # EWS income & asset certificate
    SC = "SC"
    ST = "ST"
    PWBD = "PWBD"              # disability certificate
    ESM = "ESM"                # ex-serviceman discharge / release proof
    DOMICILE = "DOMICILE"

@dataclass(frozen=True)
class CertificateRequirement:
    """What one advertisement states about one certificate kind. Unstated fields stay None."""
    kind: CertificateKind
    # The date the certificate must be valid on / held by (usually the closing date), as stated
    crucial_date: Optional[date] = None
    issued_on_or_after: Optional[date] = None
    issued_on_or_before: Optional[date] = None
    # EWS: income of this financial year, e.g. "2025-26"
    financial_year: Optional[str] = None
    # Prescribed format (e.g. "Central Government format, Annexure-III")
    format_note: Optional[str] = None
    source_page: Optional[int] = None
    source_quote: Optional[str] = None

@dataclass(frozen=True)
class HeldCertificate:
    """A certificate the candidate says they hold (verified separately from the user's own word)."""
    kind: CertificateKind
    issue_date: Optional[date] = None
    financial_year: Optional[str] = None
    valid_until: Optional[date] = None
    issuing_authority: Optional[str] = None
    central_format: Optional[bool] = None
