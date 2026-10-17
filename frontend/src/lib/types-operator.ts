/** Shapes of the operator console API (backend/api/routers/operator.py) and the account claim (POST /auth/claim). */
import type { VersionSummary } from "@/lib/types";

export interface ConsentNotice {
  purpose: string;
  notice_version: string;
  data_items: string[];
  text: string;
}

export interface ConsentIn {
  client_present: boolean;
  notice_version: string;
  data_items: string[];
  guardian_name?: string | null;
}

export interface Consent {
  id: string;
  purpose: string;
  data_items: string[];
  notice_version: string;
  method: string;
  granted_at: string;
  withdrawn_at: string | null;
  guardian_name: string | null;
}

export interface OperatorClient {
  id: string;
  full_name: string | null;
  created_at: string;
  claimed: boolean;
  consent: Consent | null;
}

/** TEST-MODE charge: no real gateway, no money collected */
export interface Charge {
  id: string;
  amount_paise: number;
  currency: string;
  provider: string;
  provider_ref: string;
  test_mode: boolean;
  at: string;
}

export interface ReportLinks {
  json: string;
  html: string;
  personalised: string | null;
}

export interface ReportSnapshot {
  kind: "vacancy" | "exam";
  id: string;
  title: string;
  organization: string | null;
  advertisement_number: string | null;
  deadline: string | null;
  document_url: string | null;
  profile_version_id: string | null;
  links: ReportLinks;
  disclaimer: string;
}

export interface OperatorReport {
  id: string;
  client_id: string;
  application_id: string;
  kind: "vacancy" | "exam";
  target_id: string;
  profile_version_id: string | null;
  snapshot: ReportSnapshot;
  created_at: string;
  charge: Charge | null;
}

export interface OperatorClientDetail extends OperatorClient {
  profile_versions: VersionSummary[];
  reports: OperatorReport[];
}

export interface ClaimTokenOut {
  token: string;
  expires_at: string;
  note: string;
}

export interface StatementEntry {
  entry_id: string;
  at: string;
  report_id: string;
  client_id: string;
  client_name: string | null;
  title: string | null;
  item: string;
  amount_paise: number;
  currency: string;
  provider: string;
  provider_ref: string;
  test_mode: boolean;
}

export interface Statement {
  month: string;
  operator_id: string;
  period_start: string;
  period_end: string;
  entries: StatementEntry[];
  report_count: number;
  total_paise: number;
  currency: string;
  test_mode: boolean;
  note: string;
}

export function rupees(paise: number): string {
  return `₹${(paise / 100).toFixed(2)}`;
}
