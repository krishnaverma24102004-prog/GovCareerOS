/**
 * Field lists of the repeating profile sections, mirroring the *In models in backend/schemas/profile.py,
 * plus the conversions between API rows and the all-string rows the form edits.
 *
 * Empty inputs are sent as null, so a blank required field reaches the API as null and comes back as a
 * 422 next to that field (the API, not the browser, decides what is valid). Number fields that do not
 * parse are sent as typed so the API reports them too.
 */
import type { ListSectionKey, PersonalInfoFull, ProfileFull } from "@/lib/types-profile-editor";

export type FieldKind = "text" | "textarea" | "date" | "number" | "url";

export interface FieldSpec {
  name: string;
  label: string;
  kind: FieldKind;
  required?: boolean;
  /** Suggestions offered through a datalist; any other value can still be typed. */
  suggest?: string[];
  wide?: boolean;
}

export interface SectionSpec {
  key: ListSectionKey;
  title: string;
  singular: string;
  fields: FieldSpec[];
  /** Fields that identify "the same row" across two versions, for the version diff. */
  identity: string[];
  /** One-line label of a row in lists and diffs. */
  label: (row: Record<string, unknown>) => string;
}

const s = (v: unknown) => (v == null || v === "" ? "" : String(v));
const join = (...parts: unknown[]) => parts.map(s).filter(Boolean).join(" · ");

export const SECTIONS: SectionSpec[] = [
  {
    key: "education",
    title: "Education",
    singular: "qualification",
    identity: ["degree", "institution"],
    label: (r) => join(r.degree, r.specialization, r.institution),
    fields: [
      { name: "degree", label: "Degree", kind: "text", required: true },
      { name: "institution", label: "Institution", kind: "text", required: true },
      { name: "university", label: "University", kind: "text" },
      { name: "specialization", label: "Specialization", kind: "text" },
      { name: "education_level", label: "Level", kind: "text", suggest: ["DIPLOMA", "BACHELORS", "MASTERS", "DOCTORATE"] },
      { name: "start_date", label: "Start date", kind: "date" },
      { name: "end_date", label: "End / result date", kind: "date" },
      { name: "percentage", label: "Percentage", kind: "number" },
      { name: "cgpa", label: "CGPA", kind: "number" },
      { name: "subjects", label: "Subjects", kind: "text", wide: true },
      { name: "equivalence", label: "Equivalence (e.g. recognised as equivalent by ...)", kind: "text", wide: true },
    ],
  },
  {
    key: "experience",
    title: "Experience",
    singular: "job",
    identity: ["organization", "designation"],
    label: (r) => join(r.designation, r.organization),
    fields: [
      { name: "organization", label: "Organization", kind: "text", required: true },
      { name: "designation", label: "Designation", kind: "text", required: true },
      { name: "employment_type", label: "Employment type", kind: "text", suggest: ["Full-time", "Part-time", "Contract", "Internship"] },
      { name: "domain", label: "Domain", kind: "text" },
      { name: "start_date", label: "Start date", kind: "date" },
      { name: "end_date", label: "End date (blank if current)", kind: "date" },
      { name: "responsibilities", label: "Responsibilities", kind: "textarea", wide: true },
      { name: "skills_used", label: "Skills used", kind: "text", wide: true },
    ],
  },
  {
    key: "projects",
    title: "Projects",
    singular: "project",
    identity: ["project_name"],
    label: (r) => join(r.project_name, r.role),
    fields: [
      { name: "project_name", label: "Project name", kind: "text", required: true },
      { name: "role", label: "Role", kind: "text" },
      { name: "domain", label: "Domain", kind: "text" },
      { name: "technologies", label: "Technologies", kind: "text" },
      { name: "start_date", label: "Start date", kind: "date" },
      { name: "end_date", label: "End date", kind: "date" },
      { name: "description", label: "Description", kind: "textarea", wide: true },
    ],
  },
  {
    key: "skills",
    title: "Skills",
    singular: "skill",
    identity: ["skill_name"],
    label: (r) => join(r.skill_name, r.proficiency),
    fields: [
      { name: "skill_name", label: "Skill", kind: "text", required: true },
      { name: "skill_type", label: "Type", kind: "text", required: true, suggest: ["Technical", "Soft", "Domain", "Language", "Tool"] },
      { name: "proficiency", label: "Proficiency", kind: "text", suggest: ["Beginner", "Intermediate", "Advanced", "Expert"] },
    ],
  },
  {
    key: "certifications",
    title: "Certifications",
    singular: "certification",
    identity: ["name", "issuer"],
    label: (r) => join(r.name, r.issuer),
    fields: [
      { name: "name", label: "Name", kind: "text", required: true },
      { name: "issuer", label: "Issuer", kind: "text", required: true },
      { name: "issue_date", label: "Issue date", kind: "date" },
      { name: "expiry_date", label: "Expiry date", kind: "date" },
      { name: "credential_id", label: "Credential ID", kind: "text" },
      { name: "verification_url", label: "Verification URL", kind: "url" },
    ],
  },
  {
    key: "languages",
    title: "Languages",
    singular: "language",
    identity: ["language"],
    label: (r) => join(r.language, r.proficiency),
    fields: [
      { name: "language", label: "Language", kind: "text", required: true },
      { name: "proficiency", label: "Proficiency", kind: "text", required: true, suggest: ["Read", "Write", "Speak", "Read, write, speak", "Native"] },
    ],
  },
  {
    key: "achievements",
    title: "Achievements",
    singular: "achievement",
    identity: ["title"],
    label: (r) => join(r.title, r.date),
    fields: [
      { name: "title", label: "Title", kind: "text", required: true },
      { name: "date", label: "Date", kind: "date" },
      { name: "description", label: "Description", kind: "textarea", wide: true },
      { name: "evidence", label: "Evidence (where it can be checked)", kind: "text", wide: true },
    ],
  },
  {
    key: "documents",
    title: "Documents",
    singular: "document",
    identity: ["document_type", "file_path"],
    label: (r) => join(r.document_type, r.file_path),
    fields: [
      { name: "document_type", label: "Document type", kind: "text", required: true, suggest: ["Degree certificate", "Marksheet", "Caste certificate", "EWS certificate", "Disability certificate", "Experience certificate", "Photo ID"] },
      { name: "file_path", label: "File path / reference", kind: "text", required: true, wide: true },
    ],
  },
];

export const SECTION_BY_KEY = Object.fromEntries(SECTIONS.map((sp) => [sp.key, sp])) as Record<ListSectionKey, SectionSpec>;

/** A row being edited: every field as a string, plus a stable React key. */
export type FormRow = { _key: string; [field: string]: string };

let seq = 0;
export const newKey = () => `new-${Date.now().toString(36)}-${(seq++).toString(36)}`;

export function emptyRow(spec: SectionSpec): FormRow {
  const row: FormRow = { _key: newKey() };
  for (const f of spec.fields) row[f.name] = "";
  return row;
}

export function toFormRows(spec: SectionSpec, rows: object[]): FormRow[] {
  return rows.map((r) => {
    const src = r as Record<string, unknown>;
    const row: FormRow = { _key: typeof src.id === "string" ? src.id : newKey() };
    for (const f of spec.fields) row[f.name] = s(src[f.name]);
    return row;
  });
}

function fieldValue(kind: FieldKind, raw: string): unknown {
  const v = raw.trim();
  if (v === "") return null;
  if (kind === "number") {
    const n = Number(v);
    return Number.isFinite(n) ? n : v;
  }
  return v;
}

export function toPayloadRows(spec: SectionSpec, rows: FormRow[]): Record<string, unknown>[] {
  return rows.map((row) => {
    const out: Record<string, unknown> = {};
    for (const f of spec.fields) out[f.name] = fieldValue(f.kind, row[f.name] ?? "");
    return out;
  });
}

/** Personal section fields in the order PersonalInfoIn declares them. */
export const PERSONAL_FIELDS: (keyof PersonalInfoFull)[] = [
  "full_name", "date_of_birth", "gender", "category", "is_pwbd", "disability_percentage", "disability_types",
  "citizenship_status", "is_ex_serviceman", "armed_forces_service_months", "esm_service_branch", "esm_enrolment_date",
  "esm_release_date", "esm_release_type", "esm_rank", "esm_civil_post_secured", "esm_reservation_used", "esm_civil_post_group",
  "esm_undertaking_given", "is_agniveer", "is_central_govt_employee",
  "central_govt_service_start", "is_state_govt_employee", "current_pay_level", "current_level_regular_since",
  "organisation_cadre", "last_deputation_end", "career_interests", "nationality", "contact", "address",
  "preferred_locations",
];

export const PERSONAL_LABELS: Record<keyof PersonalInfoFull, string> = {
  full_name: "Full name",
  date_of_birth: "Date of birth",
  gender: "Gender",
  category: "Category",
  is_pwbd: "PwBD",
  disability_percentage: "Disability percentage",
  disability_types: "Disability sub-categories",
  citizenship_status: "Citizenship",
  is_ex_serviceman: "Ex-serviceman",
  armed_forces_service_months: "Armed forces service (months)",
  esm_service_branch: "Service branch",
  esm_enrolment_date: "Enrolled on",
  esm_release_date: "Released / due for release on",
  esm_release_type: "How you left the Armed Forces",
  esm_rank: "Rank",
  esm_civil_post_secured: "Holds a regular Central Govt civil post",
  esm_reservation_used: "Civil post secured through ESM reservation",
  esm_civil_post_group: "Group of that civil post",
  esm_undertaking_given: "Self-declaration of earlier applications given",
  is_agniveer: "Former Agniveer",
  is_central_govt_employee: "Central Govt employee",
  central_govt_service_start: "Central Govt service since",
  is_state_govt_employee: "State Govt employee",
  current_pay_level: "Current pay level (regular)",
  current_level_regular_since: "Regular service in current level since",
  organisation_cadre: "Organisation / cadre",
  last_deputation_end: "Returned from last deputation on",
  career_interests: "Career interests",
  nationality: "Nationality",
  contact: "Contact",
  address: "Address",
  preferred_locations: "Preferred locations",
};

export function pickPersonal(p: PersonalInfoFull | null | undefined): PersonalInfoFull {
  const out: Record<string, unknown> = {};
  for (const k of PERSONAL_FIELDS) out[k] = p?.[k] ?? null;
  return out as PersonalInfoFull;
}

/** The row list of one section of a profile, as plain records. */
export function sectionRows(profile: ProfileFull | null, key: ListSectionKey): Record<string, unknown>[] {
  return ((profile?.[key] ?? []) as object[]).map((r) => r as Record<string, unknown>);
}

export function display(v: unknown): string {
  if (v == null || v === "") return "—";
  if (typeof v === "boolean") return v ? "yes" : "no";
  return String(v);
}
