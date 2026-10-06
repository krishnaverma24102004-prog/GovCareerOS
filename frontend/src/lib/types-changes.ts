/**
 * Official changes and deadlines (backend/services/change_log.py, deadline_helper.py, digest.py):
 *   GET /vacancies/{id}/changes, GET /exams/{id}/changes   -> VersionChanges[] (oldest notice first)
 *   GET /vacancies/{id}/deadline, GET /exams/{id}/deadline -> DeadlineInfo
 *   GET /digest/latest?format=json                        -> LatestDigest
 * Datetimes arrive as ISO strings; notice_date is an IST date.
 */

/** change_log.Evidence: where a value is stated. page is null for a listing entry's own words. */
export interface ChangeEvidence {
  document: string;
  url: string | null;
  page: number | null;
  quote: string | null;
  notice_date: string | null;
}

/** field: LAST_DATE, FEE_DATE, CORRECTION_START, CORRECTION_END, EXAM_DATE, ELIGIBILITY_TEXT, POST_ADDED, AGE_LIMIT, STATUS, CONDITION */
export interface FieldChange {
  field: string;
  label: string;
  subject: string | null;
  old: string | null;
  new: string | null;
  /** null only when nothing stood before (a post or condition the notice adds) */
  old_evidence: ChangeEvidence | null;
  new_evidence: ChangeEvidence;
  /** false: stated by the notice but not applied (needs a manual check) */
  applied: boolean;
}

export interface VersionChanges {
  version_id: string;
  change_type: string | null;
  notice_date: string | null;
  document: string;
  url: string | null;
  changes: FieldChange[];
  /** one line per change, formatted by the backend (dates in IST) */
  lines: string[];
  disclaimer: string;
}

/** deadline_helper.Stated */
export interface StatedTerm {
  value: unknown;
  document: string;
  page: number | null;
  quote: string;
}

export interface RelativeDeadline {
  status: "KNOWN" | "UNKNOWN" | string;
  closing_date: string | null;
  rule: StatedTerm | null;
  publication: StatedTerm | null;
  reasons: string[];
}

export interface ForwardingEstimate {
  estimate: string;
  buffer_days: number;
  official_deadline: string;
  label: string;
  is_official: boolean;
}

export interface DeadlineInfo {
  official: { date: string | null; evidence: ChangeEvidence | null };
  /** only when no official date is stored */
  relative_to_employment_news: RelativeDeadline | null;
  /** a labelled estimate for in-service applicants; never an official date */
  forwarding_estimate: ForwardingEstimate | null;
}

/** digest.Citation */
export interface DigestCitation {
  url: string;
  page: number;
  quote: string;
  document: string;
}

export interface DigestOpenCheck {
  label: string;
  reason: string;
  citation: DigestCitation;
}

/** digest.DigestItem; kind MATCH, DEADLINE, AGE_HEADROOM, NOTICE, CHANGE */
export interface DigestItem {
  kind: string;
  vacancy_id: string | null;
  exam_id?: string | null;
  post: string;
  organization: string;
  advertisement: string | null;
  headline: string;
  details: string[];
  citations: DigestCitation[];
  status: string | null;
  change: string | null;
  due_at: string | null;
  open_checks: DigestOpenCheck[];
}

/** Section keys and headings of digest.SECTIONS; the digest page shows official changes first. */
export const DIGEST_SECTIONS = [
  ["changes", "Official changes to what you applied for or are eligible for"],
  ["matches", "New and updated matches"],
  ["deadlines", "Closing in the next 14 days"],
  ["age_headroom", "Age limit close"],
  ["notices", "Official notices on your vacancies"],
] as const;
export type DigestSectionKey = (typeof DIGEST_SECTIONS)[number][0];

export type Digest = {
  user_id: string;
  email: string;
  name: string | null;
  profile_version_id: string | null;
  profile_version_number: number | null;
  period_start: string;
  period_end: string;
  omitted: string[];
  reported_keys: string[];
} & { [K in DigestSectionKey]?: DigestItem[] };

/** digest router DigestRunOut */
export interface DigestRun {
  id: string;
  profile_version_id: string | null;
  period_start: string;
  period_end: string;
  item_count: number;
  item_counts: Record<string, number>;
  omitted_count: number;
  sent_at: string | null;
}

export interface LatestDigest {
  run: DigestRun;
  digest: Digest;
}
