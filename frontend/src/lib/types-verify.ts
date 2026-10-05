/** Response shape of backend/api/routers/verify.py (POST /verify, the public notice check). */

/** FOUND_ON_OFFICIAL_SOURCE, LOOKALIKE_DOMAIN, UNOFFICIAL_DOMAIN or NOT_FOUND_ON_OFFICIAL_SOURCES */
export type Verdict = string;

/** A link's host against the registry's official domains (router DomainCheckOut). */
export interface DomainCheck {
  input: string;
  host: string;
  /** OFFICIAL, LOOKALIKE, UNOFFICIAL or INVALID */
  status: string;
  /** the official domain it is on, or the one it imitates */
  official_domain: string | null;
  reasons: string[];
}

/** A stored official notice or vacancy the input matches (router NoticeMatchOut). */
export interface NoticeMatch {
  /** NOTICE (link seen on an official source page) or VACANCY (parsed from the official document) */
  kind: string;
  title: string;
  organization: string;
  source: string;
  official_url: string | null;
  /** e.g. "official link", "advertisement number", "post name", "organisation", "closing date", "title" */
  matched_on: string[];
  advertisement_number: string | null;
  /** ISO date (YYYY-MM-DD) */
  closing_date: string | null;
  /** page of the official document the quote is on */
  page: number | null;
  quote: string | null;
  /** a vacancy's ACTIVE / CANCELLED */
  status: string | null;
  vacancy_id: string | null;
  notice_id: string | null;
}

/** Wording that published debunks of fake notices point to (router RedFlagOut). */
export interface RedFlag {
  code: string;
  description: string;
  quote: string;
  /** the debunks */
  citations: string[];
}

export interface VerifyResult {
  verdict: Verdict;
  message: string;
  verifier_version: string;
  url: DomainCheck | null;
  links_in_text: DomainCheck[];
  matches: NoticeMatch[];
  red_flags: RedFlag[];
  advertisement_numbers: string[];
  text_chars: number;
  pdf_pages: number | null;
  pdf_pages_read: number | null;
  /** pages read by OCR */
  ocr_pages: number[];
  /** what was not read or checked, and why */
  notes: string[];
}
