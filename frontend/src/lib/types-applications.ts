/**
 * Response shapes of backend/api/routers/applications.py (tracker) and
 * backend/services/application_prep.build_checklist (GET /applications/prep),
 * plus the list endpoints the tracker uses to pick a vacancy or an exam.
 */

export const APPLICATION_STATUSES = [
  "PLANNED",
  "APPLIED",
  "FEE_PAID",
  "ADMIT_CARD",
  "EXAM",
  "RESULT",
  "SELECTED",
  "REJECTED",
  "WITHDRAWN",
] as const;

export type ApplicationStatus = (typeof APPLICATION_STATUSES)[number];

export interface ApplicationHistory {
  from_status: string | null;
  to_status: string;
  changed_at: string;
  note: string | null;
}

export interface Application {
  id: string;
  vacancy_id: string | null;
  exam_id?: string | null;
  exam_ref: string | null;
  profile_version_id: string | null;
  status: ApplicationStatus | string;
  application_number: string | null;
  applied_at: string | null;
  fee_paid_at: string | null;
  exam_at: string | null;
  notes: string | null;
  created_at: string;
  history: ApplicationHistory[];
}

/** POST /applications body (extra fields are rejected by the backend). */
export interface ApplicationCreate {
  vacancy_id?: string | null;
  exam_id?: string | null;
  exam_ref?: string | null;
  status?: ApplicationStatus;
  profile_version_id?: string | null;
  application_number?: string | null;
  applied_at?: string | null;
  fee_paid_at?: string | null;
  exam_at?: string | null;
  notes?: string | null;
}

/** PATCH /applications/{id} body: only the keys sent are changed; a status change adds a history row. */
export interface ApplicationPatch {
  status?: ApplicationStatus;
  status_note?: string | null;
  profile_version_id?: string | null;
  application_number?: string | null;
  applied_at?: string | null;
  fee_paid_at?: string | null;
  exam_at?: string | null;
  notes?: string | null;
}

/** GET /vacancies item. */
export interface VacancySummary {
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

/** GET /exams item. */
export interface ExamSummary {
  id: string;
  conducting_body: string;
  name: string;
  exam_code: string | null;
  exam_year: number | null;
  status: string;
  application_start: string | null;
  application_end: string | null;
  notification_url: string | null;
  tentative_vacancies: number | null;
  confidence: number;
}

/** Where a checklist item comes from in the official advertisement. */
export interface PrepCitation {
  raw_document_id: string;
  document_url: string | null;
  page: number | null;
  section: string | null;
  excerpt: string | null;
}

export type PrepItemStatus = "REQUIRED" | "NOT_REQUIRED" | "IF_APPLICABLE" | "UNKNOWN";

export interface PrepDocument {
  code: string;
  applies_when: string;
  status: PrepItemStatus | string;
  reason: string;
  text: string | null;
  citation: PrepCitation;
}

export interface PrepPortal {
  url: string;
  citation: PrepCitation;
}

export interface PrepPhotoId {
  accepted: string[];
  aadhaar_advised: boolean;
  citation: PrepCitation;
}

export interface PrepPayment {
  modes: string[];
  text: string;
  citation: PrepCitation;
}

export interface PrepFee {
  status: "EXEMPT" | "PAY" | "UNKNOWN" | string;
  reason: string;
  amount: number | null;
  currency: string | null;
  exempt_groups: string[];
  citations: PrepCitation[];
}

export interface PrepRule {
  code: string;
  text?: string;
  documents?: string[];
  citation: PrepCitation;
}

export interface PrepChecklist {
  vacancy_id: string;
  profile_version_id: string;
  advertisement_number: string | null;
  application_deadline: string | null;
  portal: PrepPortal | null;
  photo_id: PrepPhotoId | null;
  payment: PrepPayment | null;
  fee: PrepFee | null;
  fee_rules: PrepRule[];
  upload_rules: PrepRule[];
  documents: PrepDocument[];
  warnings: string[];
}
