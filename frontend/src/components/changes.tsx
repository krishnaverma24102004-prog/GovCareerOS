"use client";
/**
 * Official changes and the closing date for a vacancy or an exam, shared by /vacancies/[id] and /exams/[id]:
 *   ChangeLog     GET {base}/changes: each corrigendum / addendum with every field it changed, the earlier value
 *                 with its page and quote and the new value with its page and quote
 *   DeadlinePanel GET {base}/deadline: the official last date and its evidence; when none is stored, the
 *                 Employment-News-relative date (KNOWN only when rule and publication are both stated); and,
 *                 apart from both, the in-service forwarding estimate, labelled as not official
 * `base` is "/vacancies/<id>" or "/exams/<id>".
 */
import { useEffect, useState } from "react";

import { Badge, ErrorNote, errText, fmtDate } from "@/components/ui";
import { pageLink } from "@/components/applications";
import { api } from "@/lib/api";
import type { ChangeEvidence, DeadlineInfo, FieldChange, StatedTerm, VersionChanges } from "@/lib/types-changes";

/** "page N" (or "document") linked to the official document at that page, with the quoted words. */
export function EvidenceCite({ ev, label }: { ev: ChangeEvidence | StatedTerm; label?: string }) {
  const url = "url" in ev ? ev.url : ev.document;
  const href = pageLink(url, ev.page);
  const where = ev.page ? `page ${ev.page}` : "document";
  return (
    <div className="mt-1 text-xs text-slate-600">
      {label && <span className="text-slate-500">{label}: </span>}
      {href ? (
        <a className="text-blue-700 underline" href={href} target="_blank" rel="noreferrer" title={url ?? undefined}>
          {where}
        </a>
      ) : (
        <span>{where}</span>
      )}
      {"url" in ev && ev.document && <span className="text-slate-500"> · {ev.document}</span>}
      {ev.quote && <span className="block italic text-slate-700">“{ev.quote}”</span>}
    </div>
  );
}

function useFetch<T>(path: string): { data: T | null; error: string | null } {
  const [state, setState] = useState<{ data: T | null; error: string | null }>({ data: null, error: null });
  useEffect(() => {
    let live = true;
    setState({ data: null, error: null });
    api<T>(path)
      .then((data) => live && setState({ data, error: null }))
      .catch((e) => live && setState({ data: null, error: errText(e) }));
    return () => {
      live = false;
    };
  }, [path]);
  return state;
}

export function ChangeLog({ base }: { base: string }) {
  const { data, error } = useFetch<VersionChanges[]>(`${base}/changes`);
  if (error) return <ErrorNote error={`Could not load the change log: ${error}`} />;
  if (!data) return <p className="text-sm text-slate-500">Loading changes…</p>;
  const versions = data.filter((v) => v.changes.length);
  if (!versions.length) return <p className="text-sm text-slate-500">No official notice has changed a field of this record.</p>;
  return (
    <div className="space-y-3">
      {versions.map((v) => (
        <article key={v.version_id} className="rounded border border-slate-200 bg-white p-3 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            {v.change_type && <Badge value={v.change_type} />}
            <span className="font-medium">{v.document}</span>
            {v.notice_date && <span className="text-slate-500">dated {fmtDate(v.notice_date)}</span>}
            {pageLink(v.url, null) && (
              <a className="ml-auto text-xs text-blue-700 underline" href={pageLink(v.url, null)!} target="_blank" rel="noreferrer">
                official notice
              </a>
            )}
          </div>
          <ul className="mt-2 space-y-2">
            {v.changes.map((c, i) => (
              <ChangeRow key={`${c.field}-${c.subject ?? ""}-${i}`} c={c} line={v.lines[i]} />
            ))}
          </ul>
        </article>
      ))}
      <p className="text-xs text-slate-500">{versions[0].disclaimer}</p>
    </div>
  );
}

function ChangeRow({ c, line }: { c: FieldChange; line: string | undefined }) {
  return (
    <li className="border-t border-slate-100 pt-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{c.subject ? `${c.label} (${c.subject})` : c.label}</span>
        {!c.applied && <Badge value="NOT_APPLIED" />}
      </div>
      {/* The backend's own line formats dates in IST; the raw old/new values are only a fallback */}
      {line ? <p className="text-slate-800">{line}</p> : <p className="text-slate-800">{`${c.old ?? "not stated"} → ${c.new ?? "not stated"}`}</p>}
      {!c.applied && <p className="text-xs text-amber-800">Stated by the notice but not applied here; check it in the official notice.</p>}
      <div className="grid gap-2 sm:grid-cols-2">
        <div>{c.old_evidence ? <EvidenceCite ev={c.old_evidence} label="Earlier value" /> : <p className="mt-1 text-xs text-slate-500">Nothing stated before this notice.</p>}</div>
        <div>
          <EvidenceCite ev={c.new_evidence} label="New value" />
        </div>
      </div>
    </li>
  );
}

export function DeadlinePanel({ base }: { base: string }) {
  const { data, error } = useFetch<DeadlineInfo>(`${base}/deadline`);
  if (error) return <ErrorNote error={`Could not load the closing date: ${error}`} />;
  if (!data) return <p className="text-sm text-slate-500">Loading the closing date…</p>;
  const rel = data.relative_to_employment_news;
  const est = data.forwarding_estimate;
  return (
    <div className="space-y-3 rounded border border-slate-200 bg-white p-4 text-sm">
      <div>
        <p>
          <span className="text-slate-500">Official last date: </span>
          {data.official.date ? <span className="font-medium">{fmtDate(data.official.date, true)}</span> : <span className="text-slate-600">not stored</span>}
        </p>
        {data.official.evidence && <EvidenceCite ev={data.official.evidence} label="Stated in" />}
      </div>
      {rel && (
        <div className="border-t border-slate-100 pt-2">
          <p className="flex flex-wrap items-center gap-2">
            <span className="text-slate-500">Counted from Employment News publication:</span>
            <Badge value={rel.status} />
            {rel.closing_date && <span className="font-medium">{fmtDate(rel.closing_date)}</span>}
          </p>
          {rel.rule && <EvidenceCite ev={rel.rule} label={`Rule (${String(rel.rule.value)} days)`} />}
          {rel.publication && <EvidenceCite ev={rel.publication} label="Publication" />}
          {rel.reasons.length > 0 && (
            <ul className="mt-1 list-disc pl-5 text-xs text-slate-600">
              {rel.reasons.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      {est && (
        <div className="rounded border border-amber-200 bg-amber-50 p-2">
          <p className="font-medium text-amber-900">Forwarding estimate (not an official date): {fmtDate(est.estimate)}</p>
          <p className="text-xs text-amber-900">{est.label}</p>
        </div>
      )}
    </div>
  );
}
