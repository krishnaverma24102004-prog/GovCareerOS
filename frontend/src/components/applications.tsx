"use client";
/**
 * Pieces shared by /applications and /applications/prep: status pills for the tracker and checklist
 * vocabularies, and the citation link (official document opened at the cited page).
 */
import type { PrepCitation } from "@/lib/types-applications";

const PILL: Record<string, string> = {
  // tracker
  PLANNED: "bg-slate-100 text-slate-700",
  APPLIED: "bg-blue-100 text-blue-800",
  FEE_PAID: "bg-indigo-100 text-indigo-800",
  ADMIT_CARD: "bg-violet-100 text-violet-800",
  EXAM: "bg-amber-100 text-amber-800",
  RESULT: "bg-sky-100 text-sky-800",
  SELECTED: "bg-green-100 text-green-800",
  REJECTED: "bg-red-100 text-red-800",
  WITHDRAWN: "bg-slate-200 text-slate-600",
  // checklist
  REQUIRED: "bg-amber-100 text-amber-900",
  NOT_REQUIRED: "bg-slate-100 text-slate-600",
  IF_APPLICABLE: "bg-sky-100 text-sky-800",
  UNKNOWN: "bg-orange-100 text-orange-800",
  EXEMPT: "bg-green-100 text-green-800",
  PAY: "bg-amber-100 text-amber-900",
};

export function Pill({ value }: { value: string }) {
  return (
    <span className={`inline-block whitespace-nowrap rounded px-2 py-0.5 text-xs font-medium ${PILL[value] || "bg-slate-100 text-slate-700"}`}>
      {value.replace(/_/g, " ")}
    </span>
  );
}

/** Only http(s) URLs become links; anything else is shown as text. */
export function safeHref(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    return u.protocol === "https:" || u.protocol === "http:" ? u.toString() : null;
  } catch {
    return null;
  }
}

/** PDF viewers open at a page with the #page=N fragment. */
export function pageLink(url: string | null | undefined, page: number | string | null | undefined): string | null {
  const href = safeHref(url);
  if (!href) return null;
  return page ? `${href.split("#")[0]}#page=${page}` : href;
}

/** A citation: link to the official document at the page, the section, and the quoted excerpt. */
export function Citation({ c, showExcerpt = true }: { c: PrepCitation; showExcerpt?: boolean }) {
  const href = pageLink(c.document_url, c.page);
  const label = c.page ? `page ${c.page}` : "document";
  return (
    <div className="mt-1 text-xs text-slate-600">
      <span className="text-slate-500">Source: </span>
      {href ? (
        <a className="text-blue-700 underline" href={href} target="_blank" rel="noreferrer">
          official document, {label}
        </a>
      ) : (
        <span title={c.raw_document_id}>
          {label} (document URL not recorded)
        </span>
      )}
      {c.section && <span> · {c.section}</span>}
      {showExcerpt && c.excerpt && (
        <details className="mt-0.5">
          <summary className="cursor-pointer text-slate-500">Quote from the advertisement</summary>
          <blockquote className="mt-1 whitespace-pre-wrap border-l-2 border-slate-300 pl-2 italic text-slate-700">{c.excerpt}</blockquote>
        </details>
      )}
    </div>
  );
}

/** "2026-10-01T10:00:00Z" -> value for <input type="datetime-local"> in the browser's time zone. */
export function toLocalInput(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** datetime-local value -> timezone-aware ISO string (the backend requires an offset), or null when empty. */
export function fromLocalInput(value: string): string | null {
  if (!value) return null;
  const d = new Date(value);
  return isNaN(d.getTime()) ? null : d.toISOString();
}
