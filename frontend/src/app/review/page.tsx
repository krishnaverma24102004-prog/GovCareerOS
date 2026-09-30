"use client";
/**
 * Reviewer queue for LLM extraction drafts. Every field shows the validator's verdict: the quote the
 * model gave was searched for in the document itself, and only accepted fields (verbatim quote found,
 * value parsed from it by fixed rules) can become part of a vacancy.
 *
 * Anyone signed in can read the queue; approve/reject buttons appear only for reviewers, and the
 * backend enforces that independently.
 */
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Badge, ErrorNote, Shell, errText, fmtDate, useSession } from "@/components/ui";
import { api } from "@/lib/api";
import type { DraftDetail, DraftSummary, DraftVacancy, FieldVerdict, User } from "@/lib/types";

const STATUSES = ["PENDING_REVIEW", "APPROVED", "REJECTED", "FAILED", ""] as const;

export default function ReviewPage() {
  const user = useSession();
  return <Shell user={user}>{user && <ReviewView user={user} />}</Shell>;
}

function ReviewView({ user }: { user: User }) {
  const [status, setStatus] = useState<string>("PENDING_REVIEW");
  const [drafts, setDrafts] = useState<DraftSummary[] | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setDrafts(await api<DraftSummary[]>(`/review/drafts?status_filter=${status}`));
    } catch (e) {
      setError(errText(e));
    }
  }, [status]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-4">
        <h1 className="text-xl font-semibold">Review queue</h1>
        <select className="rounded border border-slate-300 px-2 py-1 text-sm" value={status} onChange={(e) => setStatus(e.target.value)}>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s ? s.replace(/_/g, " ").toLowerCase() : "all"}
            </option>
          ))}
        </select>
        {!user.is_reviewer && <span className="text-sm text-slate-500">Read-only: your account is not a reviewer.</span>}
      </div>
      <ErrorNote error={error} />
      {drafts?.length === 0 && <p className="text-slate-500">Nothing here.</p>}
      <ul className="space-y-2">
        {drafts?.map((d) => (
          <li key={d.id} className="rounded border border-slate-200 bg-white">
            <button onClick={() => setOpenId(openId === d.id ? null : d.id)} className="flex w-full flex-wrap items-center gap-3 p-3 text-left text-sm">
              <Badge value={d.status} />
              <span className="font-medium">{d.notice_title || "(untitled notice)"}</span>
              <span className="text-slate-500">{d.source}</span>
              <span className="ml-auto text-xs text-slate-500">
                {d.vacancies} vacancies · {d.fields_accepted}/{d.fields_offered} fields verified · {d.model} · {fmtDate(d.created_at, true)}
              </span>
            </button>
            {openId === d.id && (
              <DraftPanel
                id={d.id}
                canDecide={user.is_reviewer}
                onDecided={() => {
                  setOpenId(null);
                  load();
                }}
              />
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function hasVerifiedPost(v: DraftVacancy): boolean {
  return Boolean(v.fields.post_name?.accepted);
}

function DraftPanel({ id, canDecide, onDecided }: { id: string; canDecide: boolean; onDecided: () => void }) {
  const [d, setD] = useState<DraftDetail | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<DraftDetail>(`/review/drafts/${id}`).then(setD).catch((e) => setError(errText(e)));
  }, [id]);

  async function decide(action: "approve" | "reject") {
    setError(null);
    if (action === "reject" && !note.trim()) {
      setError("A note is required to reject.");
      return;
    }
    setBusy(true);
    try {
      const body = action === "approve" ? { vacancy_indexes: [...selected].sort((a, b) => a - b), note: note || null } : { note };
      setD(await api<DraftDetail>(`/review/drafts/${id}/${action}`, { method: "POST", body }));
      onDecided();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  if (!d) return <div className="border-t border-slate-100 p-3">{error ? <ErrorNote error={error} /> : "Loading…"}</div>;
  const pending = d.status === "PENDING_REVIEW";

  return (
    <div className="space-y-4 border-t border-slate-100 p-3">
      <div className="flex flex-wrap gap-4 text-sm">
        {d.notice_url && (
          <a className="text-blue-700 underline" href={d.notice_url} target="_blank" rel="noreferrer">
            Official notice
          </a>
        )}
        <span className="text-slate-500">
          {d.provider}/{d.model} · {d.llm_seconds.toFixed(0)} s
        </span>
        {d.review_note && <span>Note: {d.review_note}</span>}
      </div>
      {d.error && <ErrorNote error={`Extraction errors: ${d.error}`} />}

      {d.payload.vacancies.map((v, i) => {
        const selectable = pending && canDecide && !v.decision && hasVerifiedPost(v);
        return (
          <div key={i} className="rounded border border-slate-200">
            <div className="flex flex-wrap items-center gap-2 bg-slate-50 px-3 py-2 text-sm">
              {selectable && (
                <input
                  type="checkbox"
                  checked={selected.has(i)}
                  onChange={(e) => {
                    const s = new Set(selected);
                    if (e.target.checked) s.add(i);
                    else s.delete(i);
                    setSelected(s);
                  }}
                />
              )}
              <span className="font-medium">
                #{i} {String(v.fields.post_name?.value ?? v.fields.post_name?.llm_value ?? "(no post name)")}
              </span>
              {v.decision && <Badge value={v.decision.status} />}
              {typeof v.decision?.vacancy_id === "string" && (
                <Link className="text-xs text-blue-700 underline" href={`/vacancies/${v.decision.vacancy_id}`}>
                  vacancy
                </Link>
              )}
              {!hasVerifiedPost(v) && <span className="text-xs text-red-700">post name not verified: cannot be approved</span>}
            </div>
            <FieldTable fields={Object.values(v.fields)} pdf={d.notice_url} />
          </div>
        );
      })}

      <ErrorNote error={error} />
      {pending && canDecide && (
        <div className="flex flex-wrap items-end gap-3">
          <label className="block grow text-sm">
            Note (required to reject)
            <input className="w-full rounded border border-slate-300 px-2 py-1" value={note} onChange={(e) => setNote(e.target.value)} />
          </label>
          <button
            disabled={busy || selected.size === 0}
            onClick={() => decide("approve")}
            className="rounded bg-green-700 px-3 py-1.5 text-sm text-white disabled:opacity-40"
          >
            Approve {selected.size} selected
          </button>
          <button disabled={busy} onClick={() => decide("reject")} className="rounded bg-red-700 px-3 py-1.5 text-sm text-white disabled:opacity-40">
            Reject remaining
          </button>
        </div>
      )}
    </div>
  );
}

function show(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  return typeof v === "string" ? v : JSON.stringify(v);
}

function FieldTable({ fields, pdf }: { fields: FieldVerdict[]; pdf: string | null }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-slate-500">
          <tr>
            <th className="px-3 py-1">Field</th>
            <th className="px-3 py-1">Verdict</th>
            <th className="px-3 py-1">Stored value</th>
            <th className="px-3 py-1">Quote found in document</th>
            <th className="px-3 py-1">Page / match</th>
          </tr>
        </thead>
        <tbody>
          {fields.map((f) => (
            <tr key={f.name} className="border-t border-slate-100 align-top">
              <td className="px-3 py-1 font-mono text-xs">{f.name}</td>
              <td className="px-3 py-1">
                <Badge value={f.accepted ? "ACCEPTED" : "REJECTED"} />
                <div className="text-xs text-slate-500">{f.reason}</div>
              </td>
              <td className="px-3 py-1">
                {show(f.value)}
                {!f.accepted && f.llm_value != null && <div className="text-xs text-slate-500">model said: {show(f.llm_value)}</div>}
              </td>
              <td className="px-3 py-1 italic text-slate-700">{f.quote ? `“${f.quote}”` : "—"}</td>
              <td className="whitespace-nowrap px-3 py-1 text-xs">
                {f.page ? (
                  pdf ? (
                    <a className="text-blue-700 underline" href={`${pdf}#page=${f.page}`} target="_blank" rel="noreferrer">
                      page {f.page}
                    </a>
                  ) : (
                    `page ${f.page}`
                  )
                ) : (
                  "—"
                )}
                {f.match && <div className="font-mono text-slate-500">{f.match}</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
