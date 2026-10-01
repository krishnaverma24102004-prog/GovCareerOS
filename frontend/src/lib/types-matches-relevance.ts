/**
 * Response shapes used by the /matches page that are not in lib/types.ts.
 * Mirrors backend/api/routers/relevance.py (RelevanceOut, ComponentOut) and
 * backend/api/routers/alerts.py (RadarRunOut).
 */

/** The six relevance components, in the order backend/relevance/scoring.py computes them. */
export const RELEVANCE_COMPONENTS = ["education", "skills", "experience", "domain", "location", "role"] as const;
export type RelevanceComponentName = (typeof RELEVANCE_COMPONENTS)[number];

export interface RelevanceComponent {
  name: RelevanceComponentName | string;
  /** 0-100, or null when the component was not scored (the evidence then says why). */
  score: number | null;
  /** Effective weight in percent after the weight of unscored components was redistributed; 0 when not scored. */
  weight: number;
  evidence: string[];
}

/** GET /relevance/{relevance_id} */
export interface RelevanceDetail {
  id: string;
  profile_version_id: string;
  vacancy_id: string;
  score: number;
  engine_version: string;
  created_at: string;
  components: RelevanceComponent[];
  /** The configured weights before redistribution, keyed by component name. */
  weights: Record<string, number>;
}

/** POST /radar/run */
export interface RadarRunResult {
  evaluations: number;
  alerts_created: Record<string, number>;
}

/** A pointer into an official document: the link opens the PDF at the cited page. */
export interface OfficialCitation {
  key: string;
  label: string;
  text: string;
  page: number | null;
  section: string | null;
  /** Official document URL as stored by the backend; null when the backend has none on record. */
  url: string | null;
}

export type EligibilityFilter = "ALL" | "ELIGIBLE" | "CONDITIONAL" | "MANUAL_REVIEW" | "INELIGIBLE";

/** Deadline windows: closing within N days, no deadline stated, or already closed. */
export type DeadlineWindow = "any" | "7" | "30" | "90" | "none" | "closed";
