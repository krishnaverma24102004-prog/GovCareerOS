/**
 * Deputation circulars (backend/api/routers/deputation.py): GET /deputation/circulars, GET /deputation/circulars/{id},
 * POST /deputation/circulars/{id}/assess. Kept tolerant (optional keys, loose `terms`) and in this one file because
 * the backend is built in parallel; the pages read every term defensively.
 */
import type { AttributionOut } from "@/lib/types-apply-ready";

/** A parsed term: its value, the page and the circular's own words. */
export interface QuotedTerm {
  value: unknown;
  page: number | null;
  quote: string;
}

export interface CircularPost {
  index: number;
  name: QuotedTerm;
  pay_level?: QuotedTerm | null;
}

/** OPEN, CLOSED or CLOSING_DATE_UNKNOWN (closing tied to an Employment News issue, or none stated) */
export type CircularStatus = "OPEN" | "CLOSED" | "CLOSING_DATE_UNKNOWN" | string;

export interface CircularSummary {
  id: string;
  raw_document_id: string;
  title: string | null;
  mode?: string[];
  closing_kind?: string | null;
  closing_date?: string | null;
  closing_days?: number | null;
  closing?: QuotedTerm | null;
  status?: CircularStatus;
  post_count: number;
  posts?: CircularPost[];
  parser_version?: string;
  parsed_at?: string;
  document_content_hash?: string | null;
  attribution?: AttributionOut | null;
  disclaimer?: string;
}

/** A service clause of one post (N years of regular service in the listed pay levels). */
export interface ServiceClauseTerm {
  years: number;
  levels: number[];
  regular: boolean;
  combined: boolean;
  page: number | null;
  quote: string;
}

/** parsing.deputation_circular.to_json: each key a QuotedTerm (or null), plus posts, documents and govt_service. */
export interface CircularTerms {
  posts?: Record<string, unknown>[];
  documents?: Record<string, QuotedTerm | null>;
  [term: string]: unknown;
}

export interface CircularDetail extends CircularSummary {
  terms: CircularTerms;
}

export interface AssessIn {
  profile_version_id: string;
  post_index?: number | null;
}

/** outcome: the rules.py vocabulary (MET, NOT_MET, UNKNOWN ...) plus NEEDS_HUMAN_REVIEW */
export interface DeputationOutcome {
  check: string;
  outcome: string;
  rule: string;
  requirement: string;
  profile_evidence: string;
  source_page?: number | null;
  note?: string | null;
  data?: Record<string, unknown>;
}

export interface PostAssessment {
  post_index: number;
  post: string;
  post_page?: number | null;
  pay_level?: number | null;
  status: string;
  decided_share?: number;
  outcomes: DeputationOutcome[];
}

export interface ForwardingChecklistItem {
  code: string;
  item: string;
  page: number | null;
  quote: string;
}

export interface DeputationAssessment {
  circular_id: string;
  profile_version_id: string;
  as_of: string;
  closing_date?: string | null;
  closing_status?: CircularStatus;
  posts: PostAssessment[];
  forwarding_checklist: ForwardingChecklistItem[];
  attribution?: AttributionOut | null;
  disclaimer?: string;
}

/** A term object as stored ({value, page, quote}); anything else is not shown as a cited term. */
export function asQuoted(v: unknown): QuotedTerm | null {
  if (!v || typeof v !== "object") return null;
  const o = v as Record<string, unknown>;
  return typeof o.quote === "string" ? { value: o.value, page: typeof o.page === "number" ? o.page : null, quote: o.quote } : null;
}

export function asServiceClauses(v: unknown): ServiceClauseTerm[] {
  return Array.isArray(v) ? v.filter((s): s is ServiceClauseTerm => !!s && typeof s === "object" && typeof (s as ServiceClauseTerm).quote === "string") : [];
}
