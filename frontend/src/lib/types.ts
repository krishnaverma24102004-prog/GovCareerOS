/** Response shapes of the FastAPI backend (backend/schemas and the router-local models). */

export interface User {
  id: string;
  email: string;
  full_name: string | null;
  is_reviewer: boolean;
}

export interface Token {
  access_token: string;
  token_type: string;
  expires_at: string;
}

export type Citizenship =
  | "INDIAN_CITIZEN"
  | "NEPAL_SUBJECT"
  | "BHUTAN_SUBJECT"
  | "TIBETAN_REFUGEE_PRE_1962"
  | "PIO_MIGRANT"
  | "OTHER";

export interface PersonalInfo {
  full_name?: string | null;
  date_of_birth?: string | null;
  gender?: string | null;
  category?: string | null;
  is_pwbd?: boolean | null;
  disability_percentage?: number | null;
  disability_types?: string | null;
  citizenship_status?: Citizenship | null;
  is_ex_serviceman?: boolean | null;
  armed_forces_service_months?: number | null;
  is_central_govt_employee?: boolean | null;
  central_govt_service_start?: string | null;
  career_interests?: string | null;
  nationality?: string | null;
  contact?: string | null;
  address?: string | null;
  preferred_locations?: string | null;
}

export interface Education {
  id: string;
  degree: string;
  institution: string;
  university: string | null;
  specialization: string | null;
  end_date: string | null;
  percentage: number | null;
  cgpa: number | null;
  education_level: string | null;
}

export interface Experience {
  id: string;
  organization: string;
  designation: string;
  start_date: string | null;
  end_date: string | null;
  domain: string | null;
}

export interface Skill {
  id: string;
  skill_name: string;
  skill_type: string;
  proficiency: string | null;
}

export interface VersionSummary {
  id: string;
  version_number: number;
  change_summary: string | null;
  created_at: string;
}

export interface Profile {
  version: VersionSummary;
  personal_info: PersonalInfo | null;
  education: Education[];
  experience: Experience[];
  skills: Skill[];
  projects: unknown[];
  certifications: unknown[];
  languages: unknown[];
  achievements: unknown[];
  documents: unknown[];
}

export type EligibilityStatus = "ELIGIBLE" | "CONDITIONAL" | "MANUAL_REVIEW" | "INELIGIBLE" | string;

export interface Match {
  vacancy_id: string;
  post_name: string;
  organization: string;
  department: string | null;
  application_deadline: string | null;
  open_for_applications: boolean;
  eligibility_status: EligibilityStatus;
  eligibility_result_id: string;
  relevance_score: number;
  relevance_id: string;
}

export interface Requirement {
  id: string;
  req_type: string;
  value: string;
  is_mandatory: boolean;
  source_page: number | null;
  source_section: string | null;
}

export interface Provenance {
  field_name: string;
  value: string | null;
  raw_document_id: string;
  page: number | null;
  section: string | null;
  excerpt: string | null;
  document_url: string | null;
}

export interface VacancyDetail {
  id: string;
  organization: string;
  ministry: string | null;
  department: string | null;
  post_name: string;
  vacancy_count: number | null;
  advertisement_number: string | null;
  external_ref: string | null;
  application_deadline: string | null;
  notification_url: string | null;
  confidence: number;
  pay_scale: string | null;
  headquarters: string | null;
  application_mode: string;
  application_start: string | null;
  eligibility_crucial_date: string | null;
  application_url: string | null;
  raw_document_id: string | null;
  requirements: Requirement[];
  provenance: Provenance[];
}

export interface EligibilityEvidence {
  check_name: string;
  outcome: string;
  match_type: string | null;
  requirement: string;
  profile_evidence: string;
  rule: string;
  requirement_id: string | null;
  source_page: string | null;
}

export interface Eligibility {
  id: string;
  profile_version_id: string;
  vacancy_id: string;
  status: EligibilityStatus;
  confidence: number;
  engine_version: string;
  crucial_date: string | null;
  created_at: string;
  evidence: EligibilityEvidence[];
}

export interface Relevance {
  id: string;
  score: number;
  engine_version: string;
  components: { name: string; score: number | null; weight: number; evidence: string[] }[];
}

export interface Alert {
  id: string;
  kind: string;
  title: string;
  body: string;
  vacancy_id: string | null;
  profile_version_id: string | null;
  eligibility_result_id: string | null;
  due_at: string | null;
  read_at: string | null;
  created_at: string;
}

export interface DraftSummary {
  id: string;
  source: string;
  notice_title: string | null;
  status: string;
  provider: string;
  model: string;
  fields_offered: number;
  fields_accepted: number;
  vacancies: number;
  llm_seconds: number;
  created_at: string;
}

/** One field as the validator judged it (backend/extraction/validator.FieldVerdict). */
export interface FieldVerdict {
  name: string;
  accepted: boolean;
  reason: string;
  llm_value: unknown;
  quote: string | null;
  page: number | null;
  value: unknown;
  match: string | null;
}

export interface DraftVacancy {
  fields: Record<string, FieldVerdict>;
  decision?: { status: string; [k: string]: unknown };
}

export interface DraftDetail extends DraftSummary {
  raw_document_id: string;
  notice_url: string | null;
  error: string | null;
  review_note: string | null;
  created_vacancy_ids: string[];
  payload: { vacancies: DraftVacancy[]; chunks?: unknown[] };
}
