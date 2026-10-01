"use client";
/**
 * Claims recorded against a profile version (GET /profile/facts) with their verification status and the
 * evidence chain of each (GET /profile/facts/{id}/evidence). Only a reviewer can verify a fact; evidence
 * the candidate attaches is shown but does not verify it.
 *
 * Every stored document is linked; PDF viewers open at a page with the #page=N fragment, so the link goes
 * to the page when the evidence records one.
 */
import { useCallback, useEffect, useState } from "react";

import { ErrorNote, errText, fmtDate } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import type { VersionSummary } from "@/lib/types";
import type { FactEvidence, ProfileFact } from "@/lib/types-profile-editor";

const FACT_STYLE: Record<string, string> = {
  VERIFIED: "bg-green-100 text-green-800",
  UNVERIFIED: "bg-amber-100 text-amber-800",
  REJECTED: "bg-red-100 text-red-800",
};

const EVIDENCE_STYLE: Record<string, string> = {
  APPROVED: "bg-green-100 text-green-800",
  DOCUMENT: "bg-blue-100 text-blue-800",
  USER_ATTESTED: "bg-slate-100 text-slate-700",
};

const EVIDENCE_LABEL: Record<string, string> = {
  APPROVED: "reviewer approved",
  DOCUMENT: "document",
  USER_ATTESTED: "self-declared",
};

function Pill({ value, styles, label }: { value: string; styles: Record<string, string>; label?: string }) {
  return (
    <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${styles[value] || "bg-slate-100 text-slate-700"}`}>
      {label ?? value.replace(/_/g, " ")}
    </span>
  );
}

/** A page number only when the evidence states one ("page 4", "p. 4"); never guessed. */
function pageOf(source: string | null): number | null {
  const m = source?.match(/\b(?:page|pg\.?|p\.)\s*(\d{1,4})\b/i);
  return m ? Number(m[1]) : null;
}

function pageLink(url: string, page: number | null): string {
  return page ? `${url.split("#")[0]}#page=${page}` : url;
}

const MAX_PARALLEL = 4;

type EvidenceState = { status: "loading" } | { status: "error"; message: string } | { status: "ok"; items: FactEvidence[] };

export function FactsPanel({ versions, versionId }: { versions: VersionSummary[]; versionId: string | null }) {
  const [selected, setSelected] = useState<string | null>(versionId);
  const [facts, setFacts] = useState<ProfileFact[] | null>(null);
  const [evidence, setEvidence] = useState<Record<string, EvidenceState>>({});
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string>("ALL");

  useEffect(() => setSelected(versionId), [versionId]);

  const load = useCallback(async (vid: string | null, live: () => boolean) => {
    setFacts(null);
    setEvidence({});
    setError(null);
    let list: ProfileFact[];
    try {
      list = await api<ProfileFact[]>(`/profile/facts${vid ? `?version_id=${encodeURIComponent(vid)}` : ""}`);
    } catch (e) {
      if (!live()) return;
      setError(e instanceof ApiError && e.status === 404 ? "No profile yet, so there are no facts." : errText(e));
      setFacts([]);
      return;
    }
    if (!live()) return;
    setFacts(list);
    setEvidence(Object.fromEntries(list.map((f) => [f.id, { status: "loading" } as EvidenceState])));
    // Evidence for every fact, a few requests at a time
    let next = 0;
    const worker = async () => {
      while (next < list.length && live()) {
        const f = list[next++];
        let state: EvidenceState;
        try {
          state = { status: "ok", items: await api<FactEvidence[]>(`/profile/facts/${f.id}/evidence`) };
        } catch (e) {
          state = { status: "error", message: errText(e) };
        }
        if (live()) setEvidence((prev) => ({ ...prev, [f.id]: state }));
      }
    };
    await Promise.all(Array.from({ length: Math.min(MAX_PARALLEL, list.length) }, worker));
  }, []);

  useEffect(() => {
    let alive = true;
    load(selected, () => alive);
    return () => {
      alive = false;
    };
  }, [selected, load]);

  const counts = (facts ?? []).reduce<Record<string, number>>((acc, f) => ({ ...acc, [f.status]: (acc[f.status] ?? 0) + 1 }), {});
  const visible = (facts ?? []).filter((f) => filter === "ALL" || f.status === filter);
  const bySubject = visible.reduce<Record<string, ProfileFact[]>>((acc, f) => ({ ...acc, [f.subject]: [...(acc[f.subject] ?? []), f] }), {});

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-4">
        <label className="text-sm">
          Profile version
          <select
            className="ml-2 rounded border border-slate-300 px-2 py-1"
            value={selected ?? ""}
            onChange={(e) => setSelected(e.target.value || null)}
            disabled={!versions.length}
          >
            {versions.map((v) => (
              <option key={v.id} value={v.id}>
                v{v.version_number} · {fmtDate(v.created_at)}
              </option>
            ))}
          </select>
        </label>
        {facts && facts.length > 0 && (
          <div className="flex flex-wrap gap-2 text-sm" role="group" aria-label="Filter by status">
            {["ALL", "VERIFIED", "UNVERIFIED", "REJECTED"].map((s) => (
              <button
                type="button"
                key={s}
                onClick={() => setFilter(s)}
                className={`rounded border px-2 py-0.5 ${filter === s ? "border-blue-600 bg-blue-50 text-blue-800" : "border-slate-300 text-slate-600"}`}
              >
                {s === "ALL" ? `All (${facts.length})` : `${s.toLowerCase()} (${counts[s] ?? 0})`}
              </button>
            ))}
          </div>
        )}
      </div>

      <p className="text-xs text-slate-500">
        Facts are the claims the resume builder and reviewers work from. Only a reviewer can mark a fact verified; documents and
        self-declarations you attach are shown as evidence but do not verify it.
      </p>

      <ErrorNote error={error} />
      {facts === null && <p className="text-sm text-slate-500">Loading facts…</p>}
      {facts && facts.length === 0 && !error && (
        <p className="text-sm text-slate-600">No facts recorded for this version yet. They are created when a resume is built from the profile.</p>
      )}
      {facts && facts.length > 0 && visible.length === 0 && <p className="text-sm text-slate-600">No {filter.toLowerCase()} facts in this version.</p>}

      {Object.entries(bySubject).map(([subject, list]) => (
        <section key={subject} className="rounded border border-slate-200 bg-white">
          <h3 className="border-b border-slate-200 px-4 py-2 font-medium capitalize">{subject.replace(/_/g, " ")}</h3>
          <ul className="divide-y divide-slate-100">
            {list.map((f) => (
              <li key={f.id} className="px-4 py-2 text-sm">
                <div className="flex flex-wrap items-baseline gap-2">
                  <Pill value={f.status} styles={FACT_STYLE} />
                  <span className="text-slate-500">{f.predicate.replace(/_/g, " ")}:</span>
                  <span className="break-words font-medium">{f.value}</span>
                  <span className="ml-auto text-xs text-slate-400">{fmtDate(f.created_at)}</span>
                </div>
                <EvidenceList state={evidence[f.id]} />
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function EvidenceList({ state }: { state: EvidenceState | undefined }) {
  if (!state || state.status === "loading") return <p className="mt-1 pl-2 text-xs text-slate-400">Loading evidence…</p>;
  if (state.status === "error") return <p className="mt-1 pl-2 text-xs text-red-700">Evidence could not be loaded: {state.message}</p>;
  if (!state.items.length) return <p className="mt-1 pl-2 text-xs text-slate-500">No evidence attached.</p>;
  return (
    <ul className="mt-1 space-y-1 border-l-2 border-slate-200 pl-3 text-xs">
      {state.items.map((ev) => {
        const page = pageOf(ev.source);
        return (
          <li key={ev.id} className="flex flex-wrap items-baseline gap-2">
            <Pill value={ev.evidence_type} styles={EVIDENCE_STYLE} label={EVIDENCE_LABEL[ev.evidence_type]} />
            {ev.source && <span className="break-words text-slate-600">{ev.source}</span>}
            {ev.artifact_url && /^https?:\/\//i.test(ev.artifact_url) ? (
              <a
                href={pageLink(ev.artifact_url, page)}
                target="_blank"
                rel="noopener noreferrer"
                className="text-blue-700 underline"
                title={ev.artifact_content_hash ? `content hash ${ev.artifact_content_hash}` : undefined}
              >
                {page ? `document, page ${page}` : "document (page not recorded)"}
              </a>
            ) : ev.artifact_url ? (
              <span className="break-all text-slate-500">{ev.artifact_url} (not a web link)</span>
            ) : ev.artifact_id ? (
              <span className="text-slate-500">stored document {ev.artifact_id.slice(0, 8)} (no URL recorded)</span>
            ) : null}
            <span className="text-slate-400">{fmtDate(ev.created_at)}</span>
          </li>
        );
      })}
    </ul>
  );
}
