/** Response shapes of the truth-locked resume API (backend/api/routers/resume.py). */

export interface ResumeRequest {
  profile_version_id: string;
  vacancy_id?: string | null;
}

/** Why a fact is believed: USER_ATTESTED (the profile row itself) or a stored document. */
export interface ResumeEvidence {
  id: string;
  evidence_type: string;
  source: string | null;
  artifact_id: string | null;
  /** Official URL the stored RawDocument was fetched from, when the evidence points at one. */
  artifact_url: string | null;
  artifact_content_hash: string | null;
}

export interface ResumeFact {
  id: string;
  /** Profile section, e.g. "education". */
  subject: string;
  /** Profile column, e.g. "degree". */
  predicate: string;
  value: string;
  /** VERIFIED (reviewer decision), UNVERIFIED, PENDING_REVIEW, … */
  status: string;
  evidence: ResumeEvidence[];
}

export interface ResumeClaim {
  id: string;
  /** Profile section name, or "meets_requirement" for lines tied to a notification requirement. */
  section: string;
  position: number;
  /** "<section>:<profile row id>" — claims sharing it form one resume item. */
  item_ref: string;
  field: string;
  /** The profile value, verbatim. */
  text: string;
  /** True only when the linked fact was verified by a reviewer. */
  verified: boolean;
  requirement_id: string | null;
  requirement_quote: string | null;
  requirement_page: number | null;
  /** MET or CONDITIONAL. */
  requirement_outcome: string | null;
  fact: ResumeFact;
}

export interface Resume {
  id: string;
  profile_version_id: string;
  vacancy_id: string | null;
  eligibility_result_id: string | null;
  builder_version: string;
  created_at: string;
  claims: ResumeClaim[];
}

export type ResumeFormat = "html" | "docx";

/** GET /vacancies item (backend/schemas/api.VacancySummary). */
export interface VacancyOption {
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
  recruitment_mode: string;
}
