"""
Attribution carried by every API response that states a vacancy, notice or exam fact: which official
source published it, the page it was found on, and the document (with its retrieval time and page).
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel

# The one place this wording lives; responses carry it as `disclaimer`
DISCLAIMER = ("Information from official notifications; the official notification prevails. GovCareerOS is not affiliated "
              "with any government body. Verify every detail in the official notification before you act on it.")

class AttributionOut(BaseModel):
    source_name: str
    organization: str
    jurisdiction: Optional[str] = None
    official_page_url: Optional[str] = None  # the official listing page the document was found on
    document_url: Optional[str] = None  # official URL the RawDocument was fetched from
    document_retrieved_at: Optional[datetime] = None
    page: Optional[int] = None  # page of the document that states the record's name, when known
