/** Response shapes of backend/api/routers/gaps.py (GET /gaps and GET /gaps/summary). */

/** One gap between a profile version and a vacancy (router GapOut). */
export interface Gap {
  id: string;
  /** e.g. AGE, EDUCATION, EXPERIENCE, CERTIFICATE, PROFILE_MISSING_INFO, RELEVANCE_<COMPONENT> */
  gap_type: string;
  reason_code: string | null;
  /** true when the gap stands between the profile and eligibility; false for conditions and relevance */
  blocking: boolean;
  requirement: string;
  /** the notification's own words for the requirement */
  requirement_quote: string | null;
  /** page of the official document the quote is on */
  requirement_page: number | null;
  requirement_id: string | null;
  profile_has: string | null;
  profile_field: string | null;
  /** deterministic next step from the gap engine */
  next_step: string | null;
  /** ISO date (YYYY-MM-DD) */
  due_date: string | null;
}

/** GET /gaps?profile_version_id=&vacancy_id= (router GapsOut). */
export interface Gaps {
  profile_version_id: string;
  vacancy_id: string;
  eligibility_result_id: string;
  eligibility_status: string;
  engine_version: string;
  gaps: Gap[];
}

/** One group of blocking gaps shared across vacancies (router BlockerOut). */
export interface Blocker {
  gap_type: string;
  reason_code: string | null;
  profile_field: string | null;
  vacancies: number;
  vacancy_ids: string[];
  /** post names, parallel to vacancy_ids */
  posts: string[];
  /** ISO date (YYYY-MM-DD) */
  earliest_due_date: string | null;
  example_next_step: string | null;
}

/** GET /gaps/summary?profile_version_id=&include_closed= (router SummaryOut). */
export interface GapsSummary {
  profile_version_id: string;
  vacancies_evaluated: number;
  vacancies_without_blockers: number;
  blockers: Blocker[];
}
