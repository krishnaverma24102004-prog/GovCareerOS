"use client";
/**
 * One draft, side by side: the validator's verdict for every field on the left, the stored document's own
 * page text on the right with each quote highlighted where the validator found it (green = accepted,
 * red = refused). Clicking a field jumps to its quote.
 *
 * Approval is one draft at a time and always goes through a confirm dialog listing exactly which vacancies
 * become vacancy records. Rejecting needs a note.
 *
 * A requirements draft (payload.kind "requirements") lists its located items instead (fee, certificates, uploads,
 * documents, key dates); the reviewer ticks the items to store, and of two items marked duplicate approves one.
 */
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";

import { Dialog } from "@/components/Dialog";
import { Badge, ErrorNote, errText } from "@/components/ui";
import { api } from "@/lib/api";
import type { DraftDetail, DraftPages, DraftRequirement, DraftVacancy, FieldVerdict, QuoteSpan } from "@/lib/types";

export type DraftCommand = { action: "approve" | "reject"; n: number } | null;

const key = (vi: number, field: string) => `${vi}:${field}`;
const reqKey = (ri: number) => `r${ri}`;
const spanKey = (s: QuoteSpan) => (s.requirement_index != null ? reqKey(s.requirement_index) : key(s.vacancy_index ?? -1, s.field));

function show(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  return typeof v === "string" ? v : JSON.stringify(v);
}

function postName(v: DraftVacancy): string {
  return String(v.fields.post_name?.value ?? v.fields.post_name?.llm_value ?? "(no post name)");
}

function approvable(v: DraftVacancy): boolean {
  return !v.decision && Boolean(v.fields.post_name?.accepted);
}

function reqApprovable(r: DraftRequirement): boolean {
  return !r.decision && r.accepted;
}

export function DraftReview({ draftId, command, onDecided }: { draftId: string; command: DraftCommand; onDecided: (msg: string) => void }) {
  const [d, setD] = useState<DraftDetail | null>(null);
  const [doc, setDoc] = useState<DraftPages | null>(null);
  const [docError, setDocError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [active, setActive] = useState<string | null>(null);
  const [dialog, setDialog] = useState<"approve" | "reject" | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const marks = useRef<Record<string, HTMLElement | null>>({});

  useEffect(() => {
    setD(null);
    setDoc(null);
    setDocError(null);
    setError(null);
    setSelected(new Set());
    setActive(null);
    setDialog(null);
    setNote("");
    let live = true;
    api<DraftDetail>(`/review/drafts/${draftId}`)
      .then((x) => live && setD(x))
      .catch((e) => live && setError(errText(e)));
    api<DraftPages>(`/review/drafts/${draftId}/pages`)
      .then((x) => live && setDoc(x))
      .catch((e) => live && setDocError(errText(e)));
    return () => {
      live = false;
    };
  }, [draftId]);

  const pending = d?.status === "PENDING_REVIEW";
  const isReq = d?.payload.kind === "requirements";
  const reqs = d?.payload.requirements || [];
  const unit = isReq ? "item(s)" : "vacancy(ies)";

  // Keyboard shortcuts from the console: a = approve (opens the confirm dialog), r = reject
  const lastCommand = useRef(0);
  useEffect(() => {
    if (!command || command.n === lastCommand.current || !d) return;
    lastCommand.current = command.n;
    if (pending) setDialog(command.action);
  }, [command, d, pending]);

  const spansByPage = useMemo(() => {
    const out: Record<number, QuoteSpan[]> = {};
    for (const s of doc?.spans || []) if (s.located && s.page !== null) (out[s.page] ||= []).push(s);
    return out;
  }, [doc]);
  const spanOf = useMemo(() => {
    const out: Record<string, QuoteSpan> = {};
    for (const s of doc?.spans || []) out[spanKey(s)] = s;
    return out;
  }, [doc]);

  const focus = useCallback((k: string) => {
    setActive(k);
    marks.current[k]?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, []);

  async function decide(action: "approve" | "reject") {
    setError(null);
    if (action === "reject" && !note.trim()) {
      setError("A note is required to reject.");
      return;
    }
    if (action === "approve" && selected.size === 0) {
      setError(`Tick at least one ${isReq ? "item" : "vacancy"} to approve.`);
      return;
    }
    setBusy(true);
    try {
      const picked = [...selected].sort((a, b) => a - b);
      const body =
        action === "approve"
          ? { [isReq ? "requirement_indexes" : "vacancy_indexes"]: picked, note: note.trim() || null }
          : { note: note.trim() };
      const out = await api<DraftDetail>(`/review/drafts/${draftId}/${action}`, { method: "POST", body });
      setD(out);
      setDialog(null);
      setSelected(new Set());
      setNote("");
      onDecided(action === "approve" ? `Approved ${picked.length} ${unit}; draft is ${out.status}` : `Draft rejected (${out.status})`);
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  if (!d) return <div className="p-3 text-sm">{error ? <ErrorNote error={error} /> : "Loading draft…"}</div>;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <Badge value={d.status} />
        <span className="font-medium">{d.notice_title || "(untitled notice)"}</span>
        <span className="text-slate-500">{d.source}</span>
        {d.notice_url && (
          <a className="text-blue-700 underline" href={d.notice_url} target="_blank" rel="noreferrer">
            official notice
          </a>
        )}
        <span className="text-xs text-slate-500">
          {d.fields_accepted}/{d.fields_offered} fields verified · {d.provider}/{d.model}
        </span>
        {pending && (
          <span className="ml-auto flex gap-2">
            <button onClick={() => setDialog("approve")} className="rounded bg-green-700 px-3 py-1 text-white">
              Approve… <kbd className="text-xs opacity-75">a</kbd>
            </button>
            <button onClick={() => setDialog("reject")} className="rounded bg-red-700 px-3 py-1 text-white">
              Reject… <kbd className="text-xs opacity-75">r</kbd>
            </button>
          </span>
        )}
      </div>
      {d.review_note && <p className="text-sm">Review note: {d.review_note}</p>}
      {d.error && <ErrorNote error={`Extraction errors: ${d.error}`} />}
      {!dialog && <ErrorNote error={error} />}

      <div className="grid gap-3 lg:grid-cols-2">
        <div className="max-h-[70vh] space-y-3 overflow-y-auto pr-1">
          {isReq && (
            <ul className="divide-y divide-slate-100 rounded border border-slate-200 bg-white text-sm">
              {reqs.map((r, ri) => {
                const k = reqKey(ri);
                const s = spanOf[k];
                return (
                  <li
                    key={ri}
                    onClick={() => s?.located && focus(k)}
                    className={`px-3 py-1.5 ${s?.located ? "cursor-pointer hover:bg-slate-50" : ""} ${active === k ? "bg-yellow-50" : ""}`}
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      {pending && reqApprovable(r) && (
                        <input
                          type="checkbox"
                          aria-label={`select item ${ri}`}
                          checked={selected.has(ri)}
                          onClick={(e) => e.stopPropagation()}
                          onChange={(e) => {
                            const next = new Set(selected);
                            if (e.target.checked) next.add(ri);
                            else next.delete(ri);
                            setSelected(next);
                          }}
                        />
                      )}
                      <span className="font-mono text-xs">
                        #{ri} {r.req_type} {r.code}
                      </span>
                      <Badge value={r.decision ? r.decision.status : r.accepted ? "ACCEPTED" : "REJECTED"} />
                      {r.page && <span className="text-xs text-slate-500">p.{r.page}</span>}
                      {r.match && <span className="font-mono text-xs text-slate-500">{r.match}</span>}
                      {r.quote && s && !s.located && <span className="text-xs text-red-700">quote not in document text</span>}
                    </div>
                    {r.duplicate_of && <div className="text-xs text-amber-700">Duplicate of {r.duplicate_of}: approve only one of them</div>}
                    <div>{show(r.value)}</div>
                    {!r.accepted && r.llm_value != null && <div className="text-xs text-slate-500">model said: {show(r.llm_value)}</div>}
                    <div className="text-xs text-slate-500">{r.reason}</div>
                  </li>
                );
              })}
            </ul>
          )}
          {d.payload.vacancies.map((v, vi) => (
            <div key={vi} className="rounded border border-slate-200 bg-white">
              <div className="flex flex-wrap items-center gap-2 bg-slate-50 px-3 py-2 text-sm">
                {pending && approvable(v) && (
                  <input
                    type="checkbox"
                    aria-label={`select vacancy ${vi}`}
                    checked={selected.has(vi)}
                    onChange={(e) => {
                      const s = new Set(selected);
                      if (e.target.checked) s.add(vi);
                      else s.delete(vi);
                      setSelected(s);
                    }}
                  />
                )}
                <span className="font-medium">
                  #{vi} {postName(v)}
                </span>
                {v.decision && <Badge value={v.decision.status} />}
                {typeof v.decision?.vacancy_id === "string" && (
                  <Link className="text-xs text-blue-700 underline" href={`/vacancies/${v.decision.vacancy_id}`}>
                    vacancy
                  </Link>
                )}
                {!v.fields.post_name?.accepted && <span className="text-xs text-red-700">post name not verified: cannot be approved</span>}
              </div>
              <ul className="divide-y divide-slate-100 text-sm">
                {Object.values(v.fields).map((f: FieldVerdict) => {
                  const k = key(vi, f.name);
                  const s = spanOf[k];
                  return (
                    <li
                      key={f.name}
                      onClick={() => s?.located && focus(k)}
                      className={`px-3 py-1.5 ${s?.located ? "cursor-pointer hover:bg-slate-50" : ""} ${active === k ? "bg-yellow-50" : ""}`}
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-xs">{f.name}</span>
                        <Badge value={f.accepted ? "ACCEPTED" : f.reason === "not stated" || f.reason === "missing" ? "NOT_STATED" : "REJECTED"} />
                        {f.page && <span className="text-xs text-slate-500">p.{f.page}</span>}
                        {f.match && <span className="font-mono text-xs text-slate-500">{f.match}</span>}
                        {f.quote && s && !s.located && <span className="text-xs text-red-700">quote not in document text</span>}
                      </div>
                      <div>{show(f.value)}</div>
                      {!f.accepted && f.llm_value != null && <div className="text-xs text-slate-500">model said: {show(f.llm_value)}</div>}
                      <div className="text-xs text-slate-500">{f.reason}</div>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </div>

        <div className="max-h-[70vh] overflow-y-auto rounded border border-slate-200 bg-white p-3">
          {docError && <ErrorNote error={`Document text unavailable: ${docError}`} />}
          {!doc && !docError && <p className="text-sm text-slate-500">Loading document text… (scanned notices are OCR&apos;d, this can take a minute)</p>}
          {doc?.pages.map((p) => (
            <section key={p.page_number} className="mb-4">
              <h3 className="sticky top-0 bg-white py-1 text-xs font-medium text-slate-500">
                Page {p.page_number}
                {p.ocr && " · OCR text"}
                {doc.document_url && (
                  <a className="ml-2 text-blue-700 underline" href={`${doc.document_url}#page=${p.page_number}`} target="_blank" rel="noreferrer">
                    open PDF page
                  </a>
                )}
              </h3>
              <PageText text={p.text} spans={spansByPage[p.page_number] || []} active={active} marks={marks} onPick={focus} />
            </section>
          ))}
        </div>
      </div>

      {dialog === "approve" && isReq && (
        <Dialog title="Approve requirement items from this draft" onClose={() => setDialog(null)}>
          <p className="text-sm text-slate-600">Only the ticked items are stored for this notice, each with its page and quote.</p>
          <ul className="space-y-1 text-sm">
            {reqs.map((r, ri) =>
              reqApprovable(r) ? (
                <li key={ri}>
                  <label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={selected.has(ri)}
                      onChange={(e) => {
                        const next = new Set(selected);
                        if (e.target.checked) next.add(ri);
                        else next.delete(ri);
                        setSelected(next);
                      }}
                    />
                    #{ri} {r.req_type} {r.code}
                    <span className="text-xs text-slate-500">(p.{r.page})</span>
                    {r.duplicate_of && <span className="text-xs text-amber-700">duplicate</span>}
                  </label>
                </li>
              ) : null,
            )}
          </ul>
          <input className="w-full rounded border border-slate-300 px-2 py-1 text-sm" placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
          <ErrorNote error={error} />
          <div className="flex justify-end gap-2">
            <button className="px-3 py-1 text-sm" onClick={() => setDialog(null)}>
              Cancel
            </button>
            <button disabled={busy || selected.size === 0} onClick={() => decide("approve")} className="rounded bg-green-700 px-3 py-1 text-sm text-white disabled:opacity-40">
              Approve {selected.size}
            </button>
          </div>
        </Dialog>
      )}
      {dialog === "approve" && !isReq && (
        <Dialog title="Approve vacancies from this draft" onClose={() => setDialog(null)}>
          <p className="text-sm text-slate-600">Only the ticked vacancies become vacancy records, with their verified fields and quotes.</p>
          <ul className="space-y-1 text-sm">
            {d.payload.vacancies.map((v, vi) =>
              approvable(v) ? (
                <li key={vi}>
                  <label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={selected.has(vi)}
                      onChange={(e) => {
                        const s = new Set(selected);
                        if (e.target.checked) s.add(vi);
                        else s.delete(vi);
                        setSelected(s);
                      }}
                    />
                    #{vi} {postName(v)}
                    <span className="text-xs text-slate-500">
                      ({Object.values(v.fields).filter((f) => f.accepted).length} verified fields)
                    </span>
                  </label>
                </li>
              ) : null,
            )}
          </ul>
          <input className="w-full rounded border border-slate-300 px-2 py-1 text-sm" placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
          <ErrorNote error={error} />
          <div className="flex justify-end gap-2">
            <button className="px-3 py-1 text-sm" onClick={() => setDialog(null)}>
              Cancel
            </button>
            <button disabled={busy || selected.size === 0} onClick={() => decide("approve")} className="rounded bg-green-700 px-3 py-1 text-sm text-white disabled:opacity-40">
              Approve {selected.size}
            </button>
          </div>
        </Dialog>
      )}
      {dialog === "reject" && (
        <Dialog title={`Reject the remaining ${isReq ? "items" : "vacancies"}`} onClose={() => setDialog(null)}>
          <p className="text-sm text-slate-600">{isReq ? "Items" : "Vacancies"} already approved stay; every undecided one is rejected.</p>
          <input
            autoFocus
            className="w-full rounded border border-slate-300 px-2 py-1 text-sm"
            placeholder="Why (required)"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && decide("reject")}
          />
          <ErrorNote error={error} />
          <div className="flex justify-end gap-2">
            <button className="px-3 py-1 text-sm" onClick={() => setDialog(null)}>
              Cancel
            </button>
            <button disabled={busy || !note.trim()} onClick={() => decide("reject")} className="rounded bg-red-700 px-3 py-1 text-sm text-white disabled:opacity-40">
              Reject
            </button>
          </div>
        </Dialog>
      )}
    </div>
  );
}

/** Page text cut at every quote boundary; overlapping quotes stack in one <mark>. */
function PageText({
  text,
  spans,
  active,
  marks,
  onPick,
}: {
  text: string;
  spans: QuoteSpan[];
  active: string | null;
  marks: RefObject<Record<string, HTMLElement | null>>;
  onPick: (k: string) => void;
}) {
  const cuts = [...new Set([0, text.length, ...spans.flatMap((s) => [s.start as number, s.end as number])])].sort((a, b) => a - b);
  const parts = [];
  for (let i = 0; i < cuts.length - 1; i++) {
    const [a, b] = [cuts[i], cuts[i + 1]];
    if (a >= b) continue;
    const covering = spans.filter((s) => (s.start as number) <= a && (s.end as number) >= b);
    if (covering.length === 0) {
      parts.push(<span key={a}>{text.slice(a, b)}</span>);
      continue;
    }
    const keys = covering.map(spanKey);
    const starting = covering.filter((s) => s.start === a).map(spanKey);
    const cls = active && keys.includes(active) ? "bg-yellow-300" : covering.some((s) => s.accepted) ? "bg-green-100" : "bg-red-100";
    parts.push(
      <mark
        key={a}
        className={`${cls} cursor-pointer rounded-sm`}
        title={covering.map((s) => `#${s.requirement_index ?? s.vacancy_index} ${s.field}${s.accepted ? "" : " (refused)"}`).join(", ")}
        onClick={() => onPick(keys[0])}
        ref={(el) => {
          for (const k of starting) marks.current[k] = el;
        }}
      >
        {text.slice(a, b)}
      </mark>,
    );
  }
  return <pre className="whitespace-pre-wrap break-words font-sans text-sm leading-relaxed">{parts}</pre>;
}
