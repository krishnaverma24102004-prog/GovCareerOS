/**
 * Full shapes of the profile API (backend/schemas/profile.py) used by the /profile editor.
 *
 * Kept apart from lib/types.ts so the editor can be developed without touching the shared file.
 * Dates are ISO "YYYY-MM-DD" strings (the backend returns plain dates for every profile date field).
 */
import type { Citizenship, VersionSummary } from "@/lib/types";

export interface PersonalInfoFull {
  full_name?: string | null;
  date_of_birth?: string | null;
  gender?: string | null;
  category?: string | null;
  is_pwbd?: boolean | null;
  disability_percentage?: number | null;
  disability_types?: string | null;
  citizenship_status?: Citizenship | null;
  is_ex_serviceman?: boolean | null;
  armed_forces_service_months?: number | null;
  is_central_govt_employee?: boolean | null;
  central_govt_service_start?: string | null;
  is_state_govt_employee?: boolean | null;
  career_interests?: string | null;
  nationality?: string | null;
  contact?: string | null;
  address?: string | null;
  preferred_locations?: string | null;
}

export interface EducationRow {
  id?: string;
  degree: string;
  institution: string;
  university: string | null;
  specialization: string | null;
  start_date: string | null;
  end_date: string | null;
  percentage: number | null;
  cgpa: number | null;
  education_level: string | null;
  subjects: string | null;
  equivalence: string | null;
}

export interface ExperienceRow {
  id?: string;
  organization: string;
  designation: string;
  employment_type: string | null;
  start_date: string | null;
  end_date: string | null;
  responsibilities: string | null;
  skills_used: string | null;
  domain: string | null;
}

export interface ProjectRow {
  id?: string;
  project_name: string;
  description: string | null;
  role: string | null;
  technologies: string | null;
  domain: string | null;
  start_date: string | null;
  end_date: string | null;
}

export interface SkillRow {
  id?: string;
  skill_name: string;
  skill_type: string;
  proficiency: string | null;
}

export interface CertificationRow {
  id?: string;
  name: string;
  issuer: string;
  issue_date: string | null;
  expiry_date: string | null;
  credential_id: string | null;
  verification_url: string | null;
}

export interface LanguageRow {
  id?: string;
  language: string;
  proficiency: string;
}

export interface AchievementRow {
  id?: string;
  title: string;
  description: string | null;
  date: string | null;
  evidence: string | null;
}

export interface DocumentRow {
  id?: string;
  document_type: string;
  file_path: string;
}

export interface ProfileFull {
  version: VersionSummary;
  personal_info: PersonalInfoFull | null;
  education: EducationRow[];
  experience: ExperienceRow[];
  projects: ProjectRow[];
  skills: SkillRow[];
  certifications: CertificationRow[];
  languages: LanguageRow[];
  achievements: AchievementRow[];
  documents: DocumentRow[];
}

export type ListSectionKey =
  | "education"
  | "experience"
  | "projects"
  | "skills"
  | "certifications"
  | "languages"
  | "achievements"
  | "documents";

export type SectionKey = "personal_info" | ListSectionKey;

/** Body of PATCH /profile: a section left out is carried over from the latest version; a sent section replaces it. */
export interface ProfilePatch {
  change_summary?: string | null;
  personal_info?: Record<string, unknown>;
  education?: Record<string, unknown>[];
  experience?: Record<string, unknown>[];
  projects?: Record<string, unknown>[];
  skills?: Record<string, unknown>[];
  certifications?: Record<string, unknown>[];
  languages?: Record<string, unknown>[];
  achievements?: Record<string, unknown>[];
  documents?: Record<string, unknown>[];
}

/** One entry of FastAPI's 422 body: {"detail": [{"loc": ["body", "education", 0, "degree"], "msg": "..."}]}. */
export interface ValidationIssue {
  loc: (string | number)[];
  msg: string;
  type?: string;
}

export type FactStatus = "UNVERIFIED" | "VERIFIED" | "REJECTED" | string;

export interface ProfileFact {
  id: string;
  profile_version_id: string;
  subject: string;
  predicate: string;
  value: string;
  status: FactStatus;
  created_at: string;
}

export type EvidenceType = "DOCUMENT" | "USER_ATTESTED" | "APPROVED" | string;

export interface FactEvidence {
  id: string;
  fact_id: string;
  evidence_type: EvidenceType;
  source: string | null;
  artifact_id: string | null;
  artifact_content_hash: string | null;
  artifact_url: string | null;
  created_at: string;
}
