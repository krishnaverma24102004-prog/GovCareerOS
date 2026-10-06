"use client";
/**
 * Pieces shared by the deputation pages: a parsed term's value in words, and its citation (the circular opened
 * at the page, with the circular's own words).
 */
import { pageLink } from "@/components/applications";
import type { QuotedTerm } from "@/lib/types-deputation";

const TERM_LABELS: Record<string, string> = {
  mode: "Mode of filling",
  max_age: "Maximum age",
  tenure_initial_years: "Initial tenure (years)",
  tenure_extension_years: "Extension (years)",
  tenure_max_years: "Maximum tenure (years)",
  closing: "Closing",
  feeder_bar: "Feeder-grade bar",
  asks_last_deputation: "Asks for return date of last deputation",
  govt_service: "Government service required",
  pay_level: "Pay level",
  analogous: "Analogous post",
  name: "Post",
};

export function termLabel(key: string): string {
  return TERM_LABELS[key] ?? key.replace(/_/g, " ").toLowerCase().replace(/^./, (c) => c.toUpperCase());
}

/** A parsed value in words: lists joined, {years, on} and {kind, date|days} spelled out, anything else as JSON. */
export function termValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string" || typeof v === "number") return String(v).replace(/_/g, " ");
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (Array.isArray(v)) return v.map(termValue).join(", ");
  if (typeof v === "object") {
    const o = v as Record<string, unknown>;
    if (o.kind === "DATE" && o.date) return `on ${String(o.date)}`;
    if (o.kind === "EMPLOYMENT_NEWS_DAYS" && o.days !== undefined) return `${String(o.days)} days from Employment News publication`;
    if (o.years !== undefined) return `${String(o.years)} years${o.on ? ` (as on ${String(o.on).replace(/_/g, " ").toLowerCase()})` : ""}`;
    return JSON.stringify(v);
  }
  return String(v);
}

export function TermCite({ page, quote, docUrl }: { page: number | null; quote: string | null; docUrl: string | null | undefined }) {
  const href = pageLink(docUrl, page);
  return (
    <div className="mt-1 text-xs text-slate-600">
      {page ? (
        href ? (
          <a className="text-blue-700 underline" href={href} target="_blank" rel="noreferrer">
            page {page}
          </a>
        ) : (
          <span>page {page}</span>
        )
      ) : (
        <span className="text-slate-400">no page</span>
      )}
      {quote && <span className="block italic text-slate-700">“{quote}”</span>}
    </div>
  );
}

export function TermRow({ name, term, docUrl }: { name: string; term: QuotedTerm; docUrl: string | null | undefined }) {
  return (
    <li className="border-t border-slate-100 pt-2 text-sm">
      <span className="text-slate-500">{termLabel(name)}: </span>
      <span className="font-medium">{termValue(term.value)}</span>
      <TermCite page={term.page} quote={term.quote} docUrl={docUrl} />
    </li>
  );
}
