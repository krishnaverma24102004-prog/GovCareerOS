# Import every model so string-based relationships ("Fact", "RawDocument", ...)
# resolve no matter which model module is imported first.
from govcareeros.backend.models.base import Base
from govcareeros.backend.models.user import User, ProfileVersion, PersonalInfo, Education, Experience, Project, Skill, Certification, Language, Achievement, UserDocument
from govcareeros.backend.models.fact import Fact, Evidence
from govcareeros.backend.models.source import GovernmentSource, SourceEndpoint, SourceAdapterVersion
from govcareeros.backend.models.artifact import RawDocument
from govcareeros.backend.models.vacancy import Vacancy, VacancyRequirement, VacancyVersion, VacancyFieldProvenance
from govcareeros.backend.models.eligibility import EligibilityResult, EligibilityEvidence, MatchScore, SkillGap
from govcareeros.backend.models.auth import BrowserSessionState
from govcareeros.backend.models.notice import RecruitmentNotice
from govcareeros.backend.models.auth_token import AuthToken
from govcareeros.backend.models.alert import UserAlert
from govcareeros.backend.models.extraction import ExtractionDraft
from govcareeros.backend.models.digest import DigestRun
from govcareeros.backend.models.exam import Exam, ExamPost, ExamRequirement, ExamFieldProvenance, ExamEligibilityResult, ExamEligibilityEvidence
from govcareeros.backend.models.exam_prep import Syllabus, QuestionPaper, StudyPlan, StudyPlanItem
from govcareeros.backend.models.application import Application, ApplicationRequirement, ApplicationStatusHistory
from govcareeros.backend.models.resume import Resume, ResumeClaim
