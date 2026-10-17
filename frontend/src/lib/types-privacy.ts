/** DPDP consent notice, consent records and erasure (backend/api/routers/privacy.py). */

export type Bilingual = { en: string; hi: string };

export type NoticeDataItem = Bilingual & { key: string };

export type NoticePurpose = Bilingual & {
  purpose: string;
  data_items: NoticeDataItem[];
};

export type ConsentNotice = {
  version: string;
  purposes: NoticePurpose[];
  withdraw: Bilingual;
  minors: Bilingual;
  grievance: Bilingual & { contact: string };
};

export type ConsentRecord = {
  id: string;
  purpose: string;
  data_items: string[];
  notice_version: string;
  method: string;
  granted_at: string;
  withdrawn_at: string | null;
  guardian_name: string | null;
};

export type Consents = {
  notice_version: string;
  active: string[];
  outdated: string[];
  records: ConsentRecord[];
};

export type EraseResult = {
  erasure_requested_at: string;
  erasure_due_at: string;
  grace_days: number;
  message: string;
};

/** The purpose that gates category, date of birth, disability, certificates and service details. */
export const ELIGIBILITY_CHECK = "ELIGIBILITY_CHECK";
