"use client";
/**
 * Fact verification for truth-locked resumes. A resume line counts as verified only when its Fact is VERIFIED,
 * so a reviewer checks the claim against the stored document in its evidence chain. The backend refuses to
 * verify a fact with no stored document, and never lets reviewers decide their own facts.
 */
import { useCallback, useEffect, useState } from "react";

import { Badge, ErrorNote, Shell, errText, fmtDate, useSession } from "@/components/ui";
import { api } from "@/lib/api";
import type { FactView, Paged } from "@/lib/types";

const PAGE = 50;
const STATUSES = ["UNVERIFIED", "VERIFIED", "REJECTED"] as const;

export default function FactsPage() {
  const user = useSession();
  return (
    <Shell user={user}>
      {user && (user.is_reviewer ? <Facts /> : <p className="text-slate-500">Fact verification is for reviewers only.</p>)}
    </Shell>
  );
}

function Facts() {
  const [status, setStatus] = useState<string>("UNVERIFIED");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<Paged<FactView> | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setPage(await api<Paged<FactView>>(`/review/facts?status=${status}&limit=${PAGE}&offset=${offset}`));
      setError(null);
    } catch (e) {
      setError(errText(e));
    }
  }, [status, offset]);

  useEffect(() => {
    load();
  }, [load]);

  const total = page?.total ?? 0;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">Profile facts</h1>
        <select
          className="rounded border border-slate-300 px-2 py-1 text-sm"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setOffset(0);
          }}
        >
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s.toLowerCase()}
            </option>
          ))}
        </select>
        <span className="text-sm text-slate-500">{total} facts</span>
      </div>
      <ErrorNote error={error} />
      {page?.items.length === 0 && <p className="text-slate-500">Nothing here.</p>}
      <ul className="space-y-2">
        {page?.items.map((f) => (
          <FactCard key={f.id} fact={f} onDecided={load} />
        ))}
      </ul>
      {total > PAGE && (
        <div className="flex items-center gap-3 text-sm">
          <button disabled={offset === 0} className="disabled:opacity-40" onClick={() => setOffset(Math.max(0, offset - PAGE))}>
            ← prev
          </button>
          <span className="text-slate-500">
            {offset + 1}–{Math.min(offset + PAGE, total)} of {total}
          </span>
          <button disabled={offset + PAGE >= total} className="disabled:opacity-40" onClick={() => setOffset(offset + PAGE)}>
            next →
          </button>
        </div>
      )}
    </div>
  );
}

function FactCard({ fact, onDecided }: { fact: FactView; onDecided: () => void }) {
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const hasDocument = fact.evidence.some((e) => e.evidence_type === "DOCUMENT" && e.artifact_id);

  async function decide(action: "verify" | "reject") {
    setError(null);
    if (action === "reject" && !note.trim()) {
      setError("A note is required to reject.");
      return;
    }
    setBusy(true);
    try {
      await api(`/review/facts/${fact.id}/${action}`, { method: "POST", body: { note: note.trim() || null } });
      onDecided();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="space-y-2 rounded border border-slate-200 bg-white p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge value={fact.status} />
        <span className="font-mono text-xs">
          {fact.subject}.{fact.predicate}
        </span>
        <span className="font-medium">{fact.value}</span>
        {fact.own && <span className="rounded bg-amber-100 px-1.5 text-xs text-amber-800">your own fact</span>}
        <span className="ml-auto text-xs text-slate-500">
          profile v{fact.version_number} · {fmtDate(fact.created_at, true)}
        </span>
      </div>
      <div>
        <div className="text-xs font-medium text-slate-500">Evidence chain</div>
        {fact.evidence.length === 0 && <p className="text-xs text-slate-500">No evidence attached.</p>}
        <ul className="ml-4 list-disc text-xs">
          {fact.evidence.map((e) => (
            <li key={e.id}>
              <span className="font-mono">{e.evidence_type}</span>
              {e.source && <> · {e.source}</>}
              {e.artifact_url && (
                <>
                  {" · "}
                  <a className="text-blue-700 underline" href={e.artifact_url} target="_blank" rel="noreferrer">
                    stored document
                  </a>
                </>
              )}
              {e.artifact_content_hash && <span className="font-mono text-slate-400"> sha {e.artifact_content_hash.slice(0, 12)}</span>}
            </li>
          ))}
        </ul>
      </div>
      {fact.review_note && <p className="text-xs text-slate-600">Review note: {fact.review_note}</p>}
      <ErrorNote error={error} />
      {!fact.own && fact.status !== "REJECTED" && (
        <div className="flex flex-wrap items-center gap-2">
          <input className="grow rounded border border-slate-300 px-2 py-1" placeholder="Note (required to reject)" value={note} onChange={(e) => setNote(e.target.value)} />
          {fact.status === "UNVERIFIED" && (
            <button
              disabled={busy || !hasDocument}
              title={hasDocument ? "" : "No stored document to verify against"}
              onClick={() => decide("verify")}
              className="rounded bg-green-700 px-3 py-1 text-white disabled:opacity-40"
            >
              Verify
            </button>
          )}
          <button disabled={busy} onClick={() => decide("reject")} className="rounded bg-red-700 px-3 py-1 text-white disabled:opacity-40">
            Reject
          </button>
        </div>
      )}
    </li>
  );
}
