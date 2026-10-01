/** Response and request shapes of the exam preparation API (backend/api/routers/prep.py). */

export interface SyllabusSubtopic {
  text: string;
  /** Page of the official syllabus document this subtopic was read from. */
  page: number;
}

export interface SyllabusTopic {
  order: number;
  title: string;
  part: string | null;
  subtopics: SyllabusSubtopic[];
}

export interface SyllabusSection {
  name: string;
  note: string | null;
  topics: SyllabusTopic[];
}

export interface Syllabus {
  exam_code: string;
  raw_document_id: string;
  /** Official URL of the notice the syllabus was parsed from. */
  notice_url: string | null;
  retrieved_at: string;
  parser_version: string;
  sections: SyllabusSection[];
}

export interface QuestionPaper {
  id: string;
  exam_code: string;
  exam_name: string;
  stage: string | null;
  year: number;
  category: string | null;
  paper_name: string;
  official_url: string;
  size_label: string | null;
  /** Set once the PDF has been downloaded and stored; only such papers are used in plans. */
  raw_document_id: string | null;
}

export type PlanItemStatus = "PENDING" | "IN_PROGRESS" | "DONE" | "SKIPPED";
export const PLAN_ITEM_STATUSES: PlanItemStatus[] = ["PENDING", "IN_PROGRESS", "DONE", "SKIPPED"];

export type PlanItemKind = "TOPIC" | "REVISION" | "PYQ";

export interface PlanIn {
  exam_code: string;
  /** YYYY-MM-DD */
  exam_date: string;
  weekly_hours: number;
  sections?: string[] | null;
  /** YYYY-MM-DD; the backend defaults to today. */
  start_date?: string | null;
}

export interface PlanItem {
  id: string;
  position: number;
  planned_week: number;
  kind: PlanItemKind | string;
  section: string | null;
  topic: string;
  planned_hours: number;
  syllabus_page: number | null;
  question_paper_id: string | null;
  status: PlanItemStatus;
  completed_at: string | null;
}

export interface StudyPlan {
  id: string;
  exam_code: string;
  start_date: string;
  target_date: string;
  weekly_hours: number;
  total_weeks: number;
  revision_weeks: number;
  sections: string[] | null;
  syllabus_raw_document_id: string;
  generator_version: string;
  items: PlanItem[];
}
