"""
Official associated domains: websites outside .gov.in / .nic.in that a registry source prints as its own in an official
document we hold. The public notice check (backend/services/notice_verifier.py) treats them as OFFICIAL with the
citation, instead of as look-alikes of the source's main domain.

Only a domain printed in a captured official document goes here, each with that document's link, page and a verbatim
quote (whitespace collapsed) of where it is printed; tests/verify/test_notice_verifier.py checks every quote against
the document's text. Domains under .gov.in / .nic.in (sscsr.gov.in, sscnr.nic.in, ssckkr.kar.nic.in) need no entry.
"""
from dataclasses import dataclass
from typing import Dict, Tuple

@dataclass(frozen=True)
class DocumentCitation:
    document: str  # the official document's title
    url: str
    fixture: str  # the captured copy under tests/fixtures
    page: int
    quote: str

@dataclass(frozen=True)
class AssociatedDomain:
    domain: str
    citations: Tuple[DocumentCitation, ...]  # first one is shown

_SSC = "https://ssc.gov.in/api/attachment/uploads/masterData/NoticeBoards/"
SSC_CGL_2026 = ("Notice of Combined Graduate Level Examination, 2026", _SSC + "Notice_of_adv_cgl_2026.pdf",
                "ssc/Notice_of_adv_cgl_2026.pdf")
SSC_CHSL_2026 = ("Notice of Combined Higher Secondary (10+2) Level Examination, 2026",
                 _SSC + "Notice_of_adv_chsle_2026.pdf", "ssc/Notice_of_adv_chsle_2026.pdf")
SSC_JE_2026 = ("Notice of Junior Engineer Examination, 2026", _SSC + "Notice_of_adv_je_2026.pdf",
               "ssc/Notice_of_adv_je_2026.pdf")

def _cite(doc: Tuple[str, str, str], page: int, quote: str) -> DocumentCitation:
    return DocumentCitation(doc[0], doc[1], doc[2], page, quote)

# Keyed by registry source name (backend/sources/registry.py). SSC's regional offices, from para 12.1 of each notice
# ("Address of the Regional Offices and their websites"). The JE notice prints https://ssccr.gov.in for the Central
# Region and the CHSL / JE notices print www.sscmpr.gov.in for the Madhya Pradesh Region (both under .gov.in);
# the CGL notice prints www.sscmpr.org.
ASSOCIATED_DOMAINS: Dict[str, Tuple[AssociatedDomain, ...]] = {
    "SSC": (
        AssociatedDomain("ssc-cr.org", (
            _cite(SSC_CGL_2026, 21, "Regional Director (CR), Staff Selection Commission,34-A, Mahatma Gandhi Marg, "
                                    "Civil Lines, Kendriya Sadan, Prayagraj – 211001. (http://www.ssc-cr.org)"),
            _cite(SSC_CHSL_2026, 14, "Regional Director (CR), Staff Selection Commission,34-A, Mahatma Gandhi Marg, "
                                     "Civil Lines, Kendriya Sadan, Prayagraj – 211001. (http://www.ssc-cr.org)"),
        )),
        AssociatedDomain("sscer.org", (
            _cite(SSC_CHSL_2026, 14, "Regional Director (ER), Staff Selection Commission, 1st MSO Building, (8th Floor), "
                                     "234/4, Acharya Jagadish Chandra Bose Road, Kolkata, West Bengal-700020 "
                                     "(www.sscer.org)"),
            _cite(SSC_CGL_2026, 22, "234/4, Acharya Jagadish Chandra Bose Road, Kolkata, West Bengal- 700020 "
                                    "(www.sscer.org)"),
            _cite(SSC_JE_2026, 18, "Regional Director (ER), Staff Selection Commission, 1st MSO Building, (8th Floor), "
                                   "234/4, Acharya Jagadish Chandra Bose Road, Kolkata, West Bengal-700020 "
                                   "(www.sscer.org)"),
        )),
        AssociatedDomain("sscmpr.org", (
            _cite(SSC_CGL_2026, 22, "Regional Director (MPR), Staff Selection Commission, 5th Floor, Investment "
                                    "Building, LIC Campus-2, Pandri, Raipur, Chhattisgarh- 492004 (www.sscmpr.org)"),
        )),
        AssociatedDomain("sscner.org.in", (
            _cite(SSC_CGL_2026, 22, "Regional Director (NER), Staff Selection Commission, Housefed Complex, Last Gate, "
                                    "Beltola-Basistha Road, P.O. Assam Sachivalaya, Dispur, Guwahati, Assam-781006 "
                                    "(www.sscner.org.in)"),
            _cite(SSC_CHSL_2026, 15, "Regional Director (NER), Staff Selection Commission, Housefed Complex, Last Gate, "
                                     "Beltola-Basistha Road, P.O. Assam Sachivalaya, Dispur, Guwahati, Assam-781006 "
                                     "(www.sscner.org.in)"),
            _cite(SSC_JE_2026, 18, "Dispur, Guwahati, Assam-781006 (www.sscner.org.in)"),
        )),
        AssociatedDomain("sscnwr.org", (
            _cite(SSC_CGL_2026, 23, "Regional Director (NWR), Staff Selection Commission, Block No. 3, Ground Floor, "
                                    "Kendriya Sadan, Sector- 9, Chandigarh-160009 (www.sscnwr.org)"),
            _cite(SSC_CHSL_2026, 15, "Regional Director (NWR), Staff Selection Commission, Block No. 3, Ground Floor, "
                                     "Kendriya Sadan, Sector- 9, Chandigarh-160009 (www.sscnwr.org)"),
        )),
        AssociatedDomain("sscwr.net", (
            _cite(SSC_CGL_2026, 23, "Regional Director (WR), Staff Selection Commission, 1st Floor, South Wing, "
                                    "Pratishtha Bhawan, 101, Maharshi Karve Road, Mumbai, Maharashtra-400020 "
                                    "(www.sscwr.net)"),
            _cite(SSC_CHSL_2026, 15, "Regional Director (WR), Staff Selection Commission, 1st Floor, South Wing, "
                                     "Pratishtha Bhawan, 101, Maharshi Karve Road, Mumbai, Maharashtra- 400020 "
                                     "(www.sscwr.net)"),
        )),
    ),
}

def associated_domains() -> Dict[str, AssociatedDomain]:
    """Every associated domain, by domain."""
    return {a.domain: a for entries in ASSOCIATED_DOMAINS.values() for a in entries}
