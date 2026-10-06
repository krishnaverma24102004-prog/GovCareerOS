/**
 * Response shape of GET /apply-ready/vacancies/{id} and GET /apply-ready/exams/{id} (the apply-ready contract
 * shared with the backend branch). Public; with a bearer token and ?profile_version_id=<own version> each item
 * also carries the candidate's status from the application_prep checklist / eligibility engine.
 */

/** The candidate's status for one item; null in the public (not personalised) view. */
export type ApplyReadyItemStatus = "OK" | "FAILS" | "NEEDS_DOCUMENT" | "CANNOT_DECIDE" | "UNKNOWN";

export interface ApplyReadyCitation {
  page: number | null;
  quote: string | null;
  document_url: string | null;
}

export interface ApplyReadyItem {
  label: string;
  detail: string | null;
  status: ApplyReadyItemStatus | null;
  citation: ApplyReadyCitation | null;
}

/** Section keys, in the order the backend returns them. */
export const APPLY_READY_SECTIONS = ["dates", "eligibility", "certificates", "fee", "uploads", "photo_id", "documents", "portal"] as const;
export type ApplyReadySectionKey = (typeof APPLY_READY_SECTIONS)[number];

export interface ApplyReadySection {
  key: ApplyReadySectionKey | string;
  title: string;
  items: ApplyReadyItem[];
}

/** backend/schemas/attribution.AttributionOut */
export interface AttributionOut {
  source_name: string;
  organization: string;
  jurisdiction?: string | null;
  official_page_url?: string | null;
  document_url?: string | null;
  document_retrieved_at?: string | null;
  page?: number | null;
}

export type ApplyReadyKind = "vacancy" | "exam";

export interface ApplyReady {
  kind: ApplyReadyKind;
  id: string;
  title: string;
  organization: string | null;
  advertisement_number: string | null;
  document_url: string | null;
  status: "OPEN" | "CLOSED" | "UNKNOWN";
  deadline: string | null;
  sections: ApplyReadySection[];
  warnings: string[];
  disclaimer: string;
  attribution: AttributionOut | null;
}

/** /apply-ready/{kind}/{id} page segment -> API collection. */
export const APPLY_READY_PATH: Record<ApplyReadyKind, string> = { vacancy: "vacancies", exam: "exams" };
