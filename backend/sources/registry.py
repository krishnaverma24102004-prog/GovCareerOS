"""
Candidate official recruitment sources (Government of India ministries, recruitment
bodies, defence/CAPF, PSU banks, autonomous bodies).

These are CANDIDATES. A source is only enabled after `scripts/verify_sources.py`
has fetched its page and found recruitment content there; unverified entries stay
disabled with their probe result recorded. URLs change often on government sites,
so re-run the verification periodically.

`parser` names a vacancy-level parser; sources without one still get notice-level
discovery (title + link + first seen), and their notices go to MANUAL_REVIEW.
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

COMMISSION = "RECRUITMENT_COMMISSION"
MINISTRY = "MINISTRY"
DEFENCE = "DEFENCE_CAPF"
BANK = "PSU_BANK_REGULATOR"
BODY = "AUTONOMOUS_BODY_PSU"
AGGREGATOR = "OFFICIAL_AGGREGATOR"

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
    SourceSpec("MIB", "Ministry of Information & Broadcasting", MINISTRY, "https://mib.gov.in/vacancies"),
    SourceSpec("MOCA", "Ministry of Civil Aviation", MINISTRY, "https://www.civilaviation.gov.in/vacancies"),
    SourceSpec("MOPSW", "Ministry of Ports, Shipping & Waterways", MINISTRY, "https://shipmin.gov.in/vacancies"),
    SourceSpec("TEXTILES", "Ministry of Textiles", MINISTRY, "https://texmin.nic.in/vacancies"),
    SourceSpec("MNRE", "Ministry of New & Renewable Energy", MINISTRY, "https://mnre.gov.in/en/vacancies/"),
    SourceSpec("DST", "Department of Science & Technology", MINISTRY, "https://dst.gov.in/vacancies"),
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
    SourceSpec("YAS", "Ministry of Youth Affairs & Sports", MINISTRY, "https://yas.gov.in/vacancies"),
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
    SourceSpec("NABARD", "National Bank for Agriculture & Rural Development", BANK, "https://www.nabard.org/careers-notices1.aspx"),
    SourceSpec("LIC", "Life Insurance Corporation of India", BANK, "https://licindia.in/careers"),

    # Autonomous bodies / scientific organisations / PSUs
    SourceSpec("DRDO", "Defence Research & Development Organisation", BODY, "https://www.drdo.gov.in/drdo/careers"),
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
]
