"""
Candidate official recruitment sources (Government of India ministries, recruitment
bodies, defence/CAPF, PSU banks, autonomous bodies) and state public service commissions.

These are CANDIDATES. A source is only enabled after `scripts/verify_sources.py`
has fetched its page and found recruitment content there; unverified entries stay
disabled with their probe result recorded. URLs change often on government sites,
so re-run the verification periodically.

`parser` names a vacancy-level parser; sources without one still get notice-level
discovery (title + link + first seen), and their notices go to MANUAL_REVIEW.

`jurisdiction` is "IN" for Union government bodies and the ISO 3166-2:IN code of the state
for a state body (HPSC -> "IN-HR"). It is derived from the category and the organisation
name, never guessed: a state body whose state is not in STATE_CODES gets None.
"""
from dataclasses import dataclass
from typing import List, Optional

@dataclass(frozen=True)
class SourceSpec:
    name: str
    organization: str
    category: str
    recruitment_url: str
    parser: Optional[str] = None

    @property
    def official_domain(self) -> str:
        from urllib.parse import urlparse
        host = urlparse(self.recruitment_url).hostname or ""
        return host[4:] if host.startswith("www.") else host

    @property
    def jurisdiction(self) -> Optional[str]:
        return jurisdiction_for(self.category, self.organization)

COMMISSION = "RECRUITMENT_COMMISSION"
MINISTRY = "MINISTRY"
DEFENCE = "DEFENCE_CAPF"
BANK = "PSU_BANK_REGULATOR"
BODY = "AUTONOMOUS_BODY_PSU"
AGGREGATOR = "OFFICIAL_AGGREGATOR"
STATE_PSC = "STATE_PSC"

# Categories whose bodies belong to a state government; every other category is a Union body
STATE_CATEGORIES = {STATE_PSC}
CENTRAL = "IN"
# ISO 3166-2:IN subdivision codes (as revised in 2020: Chhattisgarh IN-CG, Uttarakhand IN-UK), only for
# the states that have a body in SOURCES; add a state here when its body is added
STATE_CODES = {
    "Andhra Pradesh": "IN-AP",
    "Assam": "IN-AS",
    "Bihar": "IN-BR",
    "Chhattisgarh": "IN-CG",
    "Gujarat": "IN-GJ",
    "Haryana": "IN-HR",
    "Jammu & Kashmir": "IN-JK",
    "Jharkhand": "IN-JH",
    "Maharashtra": "IN-MH",
    "Punjab": "IN-PB",
    "Rajasthan": "IN-RJ",
    "Tamil Nadu": "IN-TN",
    "Uttar Pradesh": "IN-UP",
    "Uttarakhand": "IN-UK",
}
_STATE_BODY_SUFFIXES = (" Public Service Commission",)

def jurisdiction_for(category: str, organization: str) -> Optional[str]:
    """A Union body gets "IN"; a state body the code of the state its name starts with, else None."""
    if category not in STATE_CATEGORIES:
        return CENTRAL
    for suffix in _STATE_BODY_SUFFIXES:
        if organization.endswith(suffix):
            return STATE_CODES.get(organization[: -len(suffix)])
    return None

SOURCES: List[SourceSpec] = [
    # Central recruitment commissions / boards
    SourceSpec("UPSC", "Union Public Service Commission", COMMISSION, "https://www.upsc.gov.in/recruitment/recruitment-advertisement", parser="upsc_advertisement"),
    SourceSpec("UPSC-EXAMS", "Union Public Service Commission", COMMISSION, "https://www.upsc.gov.in/examinations/active-exams"),
    # JavaScript app; notices come from its public JSON API (backend/sources/ssc.py)
    SourceSpec("SSC", "Staff Selection Commission", COMMISSION, "https://ssc.gov.in/home/notice-board", parser="ssc"),
    SourceSpec("IBPS", "Institute of Banking Personnel Selection", COMMISSION, "https://www.ibps.in/"),
    # rrbcdg.gov.in redirects here; the board's pages now live on the Indian Railways domain
    SourceSpec("RRB-CDG", "Railway Recruitment Board Chandigarh", COMMISSION, "https://rrb.indianrailways.gov.in/chandigarh"),
    SourceSpec("NTA", "National Testing Agency", COMMISSION, "https://nta.ac.in/"),

    # Official aggregators covering all ministries
    SourceSpec("EMPLOYMENT-NEWS", "Employment News (Ministry of Information & Broadcasting)", AGGREGATOR, "https://www.employmentnews.gov.in/NewEmp/Home.aspx"),
    SourceSpec("NCS", "National Career Service (Ministry of Labour & Employment)", AGGREGATOR, "https://www.ncs.gov.in/"),

    # Ministries / departments — vacancy & deputation circular pages
    SourceSpec("MHA", "Ministry of Home Affairs", MINISTRY, "https://www.mha.gov.in/en/notifications/vacancies"),
    SourceSpec("MEA", "Ministry of External Affairs", MINISTRY, "https://www.mea.gov.in/vacancies.htm"),
    SourceSpec("MOD", "Ministry of Defence", MINISTRY, "https://www.mod.gov.in/en/recruitment"),
    SourceSpec("DEA", "Department of Economic Affairs, Ministry of Finance", MINISTRY, "https://dea.gov.in/recruitment"),
    SourceSpec("DOE", "Department of Expenditure, Ministry of Finance", MINISTRY, "https://doe.gov.in/vacancies"),
    SourceSpec("DFS", "Department of Financial Services, Ministry of Finance", MINISTRY, "https://financialservices.gov.in/beta/en/vacancies"),
    SourceSpec("MOHFW", "Ministry of Health & Family Welfare", MINISTRY, "https://mohfw.gov.in/?q=vacancies"),
    SourceSpec("MOE", "Ministry of Education", MINISTRY, "https://www.education.gov.in/vacancies"),
    SourceSpec("DOPT", "Department of Personnel & Training", MINISTRY, "https://dopt.gov.in/vacancies"),
    SourceSpec("LEGAL-AFFAIRS", "Department of Legal Affairs, Ministry of Law & Justice", MINISTRY, "https://legalaffairs.gov.in/vacancies"),
    SourceSpec("MEITY", "Ministry of Electronics & IT", MINISTRY, "https://www.meity.gov.in/vacancies"),
    SourceSpec("MORTH", "Ministry of Road Transport & Highways", MINISTRY, "https://morth.nic.in/vacancies"),
    SourceSpec("LABOUR", "Ministry of Labour & Employment", MINISTRY, "https://labour.gov.in/vacancies"),
    SourceSpec("AGRIWELFARE", "Department of Agriculture & Farmers Welfare", MINISTRY, "https://agriwelfare.gov.in/en/Recruitment"),
    SourceSpec("POWER", "Ministry of Power", MINISTRY, "https://powermin.gov.in/en/vacancies"),
    SourceSpec("MOEFCC", "Ministry of Environment, Forest & Climate Change", MINISTRY, "https://moef.gov.in/vacancies"),
    SourceSpec("MOSPI", "Ministry of Statistics & Programme Implementation", MINISTRY, "https://www.mospi.gov.in/vacancies"),
    SourceSpec("MIB", "Ministry of Information & Broadcasting", MINISTRY, "https://mib.gov.in/en/offerings/vacancies"),
    SourceSpec("MOCA", "Ministry of Civil Aviation", MINISTRY, "https://www.civilaviation.gov.in/vacancies"),
    SourceSpec("MOPSW", "Ministry of Ports, Shipping & Waterways", MINISTRY, "https://shipmin.gov.in/vacancies"),
    SourceSpec("TEXTILES", "Ministry of Textiles", MINISTRY, "https://texmin.nic.in/vacancies"),
    SourceSpec("MNRE", "Ministry of New & Renewable Energy", MINISTRY, "https://mnre.gov.in/en/vacancies/"),
    SourceSpec("DST", "Department of Science & Technology", MINISTRY, "https://dst.gov.in/administrationfinance/recruitment-cell"),
    SourceSpec("DBT", "Department of Biotechnology", MINISTRY, "https://dbtindia.gov.in/vacancies"),
    SourceSpec("CULTURE", "Ministry of Culture", MINISTRY, "https://www.indiaculture.gov.in/vacancies"),
    SourceSpec("TOURISM", "Ministry of Tourism", MINISTRY, "https://tourism.gov.in/vacancies"),
    SourceSpec("COAL", "Ministry of Coal", MINISTRY, "https://coal.gov.in/en/vacancies"),
    SourceSpec("MINES", "Ministry of Mines", MINISTRY, "https://mines.gov.in/webportal/vacancies"),
    SourceSpec("MOWR", "Department of Water Resources, Ministry of Jal Shakti", MINISTRY, "https://jalshakti-dowr.gov.in/vacancies/"),
    SourceSpec("RURAL", "Ministry of Rural Development", MINISTRY, "https://rural.gov.in/en/vacancies"),
    SourceSpec("MOHUA", "Ministry of Housing & Urban Affairs", MINISTRY, "https://mohua.gov.in/cms/vacancies.php"),
    SourceSpec("WCD", "Ministry of Women & Child Development", MINISTRY, "https://wcd.gov.in/vacancies"),
    SourceSpec("SOCIAL-JUSTICE", "Ministry of Social Justice & Empowerment", MINISTRY, "https://socialjustice.gov.in/vacancies"),
    SourceSpec("TRIBAL", "Ministry of Tribal Affairs", MINISTRY, "https://tribal.nic.in/vacancies.aspx"),
    SourceSpec("MSME", "Ministry of Micro, Small & Medium Enterprises", MINISTRY, "https://msme.gov.in/vacancies"),
    SourceSpec("COMMERCE", "Department of Commerce", MINISTRY, "https://commerce.gov.in/vacancies/"),
    SourceSpec("DPIIT", "Department for Promotion of Industry & Internal Trade", MINISTRY, "https://dpiit.gov.in/vacancies"),
    SourceSpec("PETROLEUM", "Ministry of Petroleum & Natural Gas", MINISTRY, "https://mopng.gov.in/en/vacancies"),
    SourceSpec("STEEL", "Ministry of Steel", MINISTRY, "https://steel.gov.in/vacancies"),
    SourceSpec("TELECOM", "Department of Telecommunications", MINISTRY, "https://dot.gov.in/vacancies"),
    SourceSpec("POSTS", "Department of Posts", MINISTRY, "https://www.indiapost.gov.in/VAS/Pages/Recruitment.aspx"),
    SourceSpec("CONSUMER-AFFAIRS", "Department of Consumer Affairs", MINISTRY, "https://consumeraffairs.nic.in/vacancies"),
    SourceSpec("FOOD-PD", "Department of Food & Public Distribution", MINISTRY, "https://dfpd.gov.in/vacancies.htm"),
    # No vacancies page; the English homepage lists the ministry's recruitment notices
    SourceSpec("YAS", "Ministry of Youth Affairs & Sports", MINISTRY, "https://yas.gov.in/en"),
    SourceSpec("PANCHAYATI-RAJ", "Ministry of Panchayati Raj", MINISTRY, "https://panchayat.gov.in/vacancies"),
    SourceSpec("MINORITY", "Ministry of Minority Affairs", MINISTRY, "https://minorityaffairs.gov.in/vacancies"),
    SourceSpec("PARLIAMENTARY", "Ministry of Parliamentary Affairs", MINISTRY, "https://mpa.gov.in/vacancies"),
    SourceSpec("EXTERNAL-CAG", "Comptroller & Auditor General of India", MINISTRY, "https://cag.gov.in/en/recruitment"),

    # Defence and Central Armed Police Forces
    SourceSpec("ARMY", "Indian Army", DEFENCE, "https://joinindianarmy.nic.in/"),
    SourceSpec("NAVY", "Indian Navy", DEFENCE, "https://www.joinindiannavy.gov.in/"),
    SourceSpec("AIRFORCE", "Indian Air Force", DEFENCE, "https://careerindianairforce.cdac.in/"),
    SourceSpec("BSF", "Border Security Force", DEFENCE, "https://rectt.bsf.gov.in/"),
    SourceSpec("CRPF", "Central Reserve Police Force", DEFENCE, "https://rect.crpf.gov.in/"),
    SourceSpec("CISF", "Central Industrial Security Force", DEFENCE, "https://cisfrectt.cisf.gov.in/"),
    SourceSpec("ITBP", "Indo-Tibetan Border Police", DEFENCE, "https://recruitment.itbpolice.nic.in/"),

    # Banking regulators / PSU banks / insurance
    SourceSpec("RBI", "Reserve Bank of India", BANK, "https://opportunities.rbi.org.in/Scripts/Vacancies.aspx"),
    SourceSpec("SBI", "State Bank of India", BANK, "https://sbi.co.in/web/careers/current-openings"),
    # Found through the site's sitemap.xml
    SourceSpec("NABARD", "National Bank for Agriculture & Rural Development", BANK, "https://www.nabard.org/careers-notices1.aspx?cid=693&id=26"),
    SourceSpec("LIC", "Life Insurance Corporation of India", BANK, "https://licindia.in/careers"),

    # Autonomous bodies / scientific organisations / PSUs
    # The careers page is gone; the English homepage lists advertisements (the Hindi one lives on an IDN domain)
    SourceSpec("DRDO", "Defence Research & Development Organisation", BODY, "https://drdo.gov.in/drdo/en/"),
    SourceSpec("ISRO", "Indian Space Research Organisation", BODY, "https://www.isro.gov.in/Careers.html"),
    SourceSpec("BARC", "Bhabha Atomic Research Centre", BODY, "https://www.barc.gov.in/careers/"),
    SourceSpec("AIIMS", "All India Institute of Medical Sciences, New Delhi", BODY, "https://www.aiimsexams.ac.in/"),
    SourceSpec("ESIC", "Employees' State Insurance Corporation", BODY, "https://www.esic.gov.in/recruitments"),
    SourceSpec("EPFO", "Employees' Provident Fund Organisation", BODY, "https://www.epfindia.gov.in/site_en/Recruitments.php"),
    SourceSpec("FCI", "Food Corporation of India", BODY, "https://fci.gov.in/recruitments.php"),
    SourceSpec("KVS", "Kendriya Vidyalaya Sangathan", BODY, "https://kvsangathan.nic.in/"),
    SourceSpec("NVS", "Navodaya Vidyalaya Samiti", BODY, "https://navodaya.gov.in/nvs/en/Recruitment/"),
    SourceSpec("CSIR", "Council of Scientific & Industrial Research", BODY, "https://www.csir.res.in/en/career-opportunities"),
    SourceSpec("ICAR", "Indian Council of Agricultural Research", BODY, "https://icar.org.in/vacancies"),
    SourceSpec("NHAI", "National Highways Authority of India", BODY, "https://nhai.gov.in/#/vacancies/current"),

    # State public service commissions: each URL returned recruitment links on its own domain (live probe
    # 2026-09-30). Left out: KPSC Karnataka (malformed HTTP header), WBPSC/HPPSC/PSSSB (servers need
    # insecure legacy TLS renegotiation), MPPSC (robots.txt disallows), TGPSC (redirects to http),
    # Kerala PSC/OPSC/OSSC (no recruitment links in the served HTML).
    SourceSpec("UPPSC", "Uttar Pradesh Public Service Commission", STATE_PSC, "https://uppsc.up.nic.in/"),
    SourceSpec("BPSC", "Bihar Public Service Commission", STATE_PSC, "https://bpsc.bihar.gov.in/"),
    SourceSpec("MPSC", "Maharashtra Public Service Commission", STATE_PSC, "https://mpsc.gov.in/adv_notification/8"),
    SourceSpec("RPSC", "Rajasthan Public Service Commission", STATE_PSC, "https://rpsc.rajasthan.gov.in/advertisements"),
    SourceSpec("TNPSC", "Tamil Nadu Public Service Commission", STATE_PSC, "https://www.tnpsc.gov.in/English/notification.aspx"),
    SourceSpec("GPSC", "Gujarat Public Service Commission", STATE_PSC, "https://gpsc.gujarat.gov.in/"),
    SourceSpec("HPSC", "Haryana Public Service Commission", STATE_PSC, "https://hpsc.gov.in/en-us/"),
    SourceSpec("PPSC", "Punjab Public Service Commission", STATE_PSC, "https://ppsc.gov.in/"),
    SourceSpec("APPSC", "Andhra Pradesh Public Service Commission", STATE_PSC, "https://portal-psc.ap.gov.in/HomePages/RecruitmentNotifications"),
    SourceSpec("UKPSC", "Uttarakhand Public Service Commission", STATE_PSC, "https://psc.uk.gov.in/candidate-corner/recruitment"),
    SourceSpec("APSC", "Assam Public Service Commission", STATE_PSC, "https://apsc.nic.in/"),
    SourceSpec("JKPSC", "Jammu & Kashmir Public Service Commission", STATE_PSC, "https://jkpsc.nic.in/"),
    SourceSpec("JPSC", "Jharkhand Public Service Commission", STATE_PSC, "https://www.jpsc.gov.in/"),
    SourceSpec("CGPSC", "Chhattisgarh Public Service Commission", STATE_PSC, "https://psc.cg.gov.in/Advertisement.php"),
]
