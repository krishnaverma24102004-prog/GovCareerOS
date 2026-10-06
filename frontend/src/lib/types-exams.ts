/**
 * Response shapes of backend/api/routers/exams.py (kept apart from lib/types.ts so exam work merges cleanly).
 * Datetimes arrive as ISO strings; UUIDs as strings.
 */
import type { EligibilityEvidence, EligibilityStatus, Provenance, Requirement } from "@/lib/types";

/** Exam.status: NOTIFIED or CANCELLED, set only from an official notice. */
export type ExamStatus = "NOTIFIED" | "CANCELLED" | string;

/** GET /exams — one row of the list. */
export interface ExamSummary {
  id: string;
  conducting_body: string;
  name: string;
  exam_code: string | null;
  exam_year: number | null;
  status: ExamStatus;
  application_start: string | null;
  application_end: string | null;
  notification_url: string | null;
  tentative_vacancies: number | null;
  confidence: number;
}

/** A post filled through the exam, as the notice states it (age/education only when post-wise). */
export interface ExamPost {
  id: string;
  post_name: string;
  post_code: string | null;
  department: string | null;
  pay_level: string | null;
  pay_scale: string | null;
  age_min: number | null;
  age_max: number | null;
  education_text: string | null;
  vacancy_count: number | null;
  source_page: number | null;
  source_excerpt: string | null;
}

/** One stage of the exam schedule, e.g. {"stage": "Tier-I (Computer Based Examination)", "schedule": "To be notified later"}. */
export interface ExamDateStage {
  stage?: string;
  schedule?: string;
  [k: string]: unknown;
}

/** GET /exams/{id} */
export interface ExamDetail extends ExamSummary {
  external_ref: string;
  raw_document_id: string | null;
  fee_payment_end: string | null;
  correction_start: string | null;
  correction_end: string | null;
  fee_amount: number | null;
  fee_exemption: string | null;
  exam_dates: ExamDateStage[] | Record<string, unknown> | null;
  age_crucial_date: string | null;
  education_cutoff_date: string | null;
  education_text: string | null;
  posts: ExamPost[];
  requirements: Requirement[];
  provenance: Provenance[];
}

/** POST /exams/{id}/eligibility body: an explicit profile version, optionally one post of the exam. */
export interface ExamEligibilityIn {
  profile_version_id: string;
  post_id?: string | null;
}

/** POST /exams/{id}/eligibility (201) and GET /exams/{id}/eligibility[/{result_id}] */
export interface ExamEligibility {
  id: string;
  exam_id: string;
  post_id: string | null;
  profile_version_id: string;
  status: EligibilityStatus;
  confidence: number;
  engine_version: string;
  crucial_date: string | null;
  created_at: string;
  evidence: EligibilityEvidence[];
  esm?: EligibilityEvidence[];
  esm_status?: string | null;
}
