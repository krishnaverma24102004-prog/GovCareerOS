"use client";
/**
 * New per-application report: pick one of the operator's clients and a stored vacancy or exam. The backend creates
 * the client's application (or reuses it), snapshots the apply-ready links and charges the report once in TEST MODE.
 *
 * Each visit to this page gets one idempotency key, so a double click or a retried request returns the same report
 * and the same single charge instead of a second one.
 */
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState, type FormEvent } from "react";

import { ErrorNote, Shell, errText, fmtDate, useSession } from "@/components/ui";
import { OperatorNav } from "@/components/operator";
import { api } from "@/lib/api";
import type { ExamSummary, VacancySummary } from "@/lib/types-applications";
import type { OperatorClient, OperatorReport } from "@/lib/types-operator";

export default function NewReportPage() {
  const user = useSession();
  return (
    <Shell user={user}>
      {user &&
        (user.is_operator ? (
          <Suspense fallback={<p className="text-slate-500">Loading…</p>}>
            <NewReport />
          </Suspense>
        ) : (
          <p className="text-slate-500">The operator console is for registered operators only.</p>
        ))}
    </Shell>
  );
}

function newKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

type Target = { kind: "vacancy" | "exam"; id: string; label: string; deadline: string | null };

function NewReport() {
  const params = useSearchParams();
  const router = useRouter();
  const [clients, setClients] = useState<OperatorClient[] | null>(null);
  const [clientId, setClientId] = useState(params.get("client") ?? "");
  const [kind, setKind] = useState<"vacancy" | "exam">("vacancy");
  const [q, setQ] = useState("");
  const [results, setResults] = useState<Target[]>([]);
  const [target, setTarget] = useState<Target | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const key = useMemo(newKey, []);

  useEffect(() => {
    api<OperatorClient[]>("/operator/clients")
      .then(setClients)
      .catch((e) => setError(errText(e)));
  }, []);

  async function search(e: FormEvent) {
    e.preventDefault();
    setError(null);
    const query = encodeURIComponent(q.trim());
    try {
      if (kind === "vacancy") {
        const vs = await api<VacancySummary[]>(`/vacancies?limit=50${query ? `&q=${query}` : ""}`);
        setResults(vs.map((v) => ({ kind, id: v.id, label: `${v.post_name}, ${v.organization}${v.advertisement_number ? ` (Advt. ${v.advertisement_number})` : ""}`, deadline: v.application_deadline })));
      } else {
        const es = await api<ExamSummary[]>(`/exams?limit=50${query ? `&q=${query}` : ""}`);
        setResults(es.map((x) => ({ kind, id: x.id, label: `${x.name}, ${x.conducting_body}`, deadline: x.application_end })));
      }
      setTarget(null);
    } catch (err) {
      setError(errText(err));
    }
  }

  async function create() {
    if (!clientId || !target) return;
    setBusy(true);
    setError(null);
    try {
      await api<OperatorReport>(`/operator/clients/${clientId}/reports`, {
        method: "POST",
        body: { kind: target.kind, target_id: target.id, idempotency_key: key },
      });
      router.push(`/operator/clients/${clientId}`);
    } catch (err) {
      setError(errText(err));
      setBusy(false);
    }
  }

  const eligibleClients = clients?.filter((c) => c.consent) ?? [];
  return (
    <div className="space-y-4">
      <OperatorNav />
      <h1 className="text-xl font-semibold">New report</h1>
      <ErrorNote error={error} />
      <label className="block text-sm">
        Client
        <select className="mt-1 block w-full rounded border border-slate-300 px-2 py-1" value={clientId} onChange={(e) => setClientId(e.target.value)}>
          <option value="">choose a client with active consent</option>
          {eligibleClients.map((c) => (
            <option key={c.id} value={c.id}>
              {c.full_name || c.id.slice(0, 8)}
            </option>
          ))}
        </select>
      </label>
      {clients && eligibleClients.length === 0 && (
        <p className="text-sm text-slate-500">
          No client has an active consent. <Link className="text-blue-700" href="/operator">Add a client</Link> first.
        </p>
      )}
      <form onSubmit={search} className="flex flex-wrap items-end gap-2 text-sm">
        <select className="rounded border border-slate-300 px-2 py-1" value={kind} onChange={(e) => setKind(e.target.value as "vacancy" | "exam")}>
          <option value="vacancy">Vacancy</option>
          <option value="exam">Exam</option>
        </select>
        <input
          className="flex-1 rounded border border-slate-300 px-2 py-1"
          placeholder={kind === "vacancy" ? "post, ministry or department" : "exam name or code"}
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <button className="rounded border border-slate-300 px-3 py-1">Search</button>
      </form>
      <ul className="divide-y divide-slate-100 rounded border border-slate-200 bg-white text-sm">
        {results.map((t) => (
          <li key={t.id}>
            <label className="flex cursor-pointer items-center gap-2 px-3 py-1">
              <input type="radio" name="target" checked={target?.id === t.id} onChange={() => setTarget(t)} />
              <span>{t.label}</span>
              <span className="ml-auto text-xs text-slate-500">last date {fmtDate(t.deadline)}</span>
            </label>
          </li>
        ))}
      </ul>
      <div className="flex items-center gap-3">
        <button disabled={!clientId || !target || busy} onClick={create} className="rounded bg-blue-700 px-3 py-1 text-sm text-white disabled:opacity-40">
          Create report
        </button>
        <span className="text-xs text-amber-800">Billing is in TEST MODE: the charge is recorded, no money is collected.</span>
      </div>
    </div>
  );
}
