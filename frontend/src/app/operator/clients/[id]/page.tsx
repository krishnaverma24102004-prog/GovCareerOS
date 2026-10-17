"use client";
/**
 * One client of the signed-in operator: consent (withdraw / record again with the client present), the client's
 * basic profile details (saved through the ordinary profile service, a new version each time), the one-time account
 * claim code, and the client's per-application reports with their TEST-MODE charges and apply-ready links.
 *
 * Another operator's client is a 404 from the backend; this page then shows that error.
 */
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { Badge, ErrorNote, Shell, errText, fmtDate, useSession } from "@/components/ui";
import { safeHref } from "@/components/applications";
import { ConsentForm, OperatorNav } from "@/components/operator";
import { api, API_URL } from "@/lib/api";
import type { PrepChecklist } from "@/lib/types-applications";
import type { PersonalInfoFull } from "@/lib/types-profile-editor";
import { rupees, type ClaimTokenOut, type ConsentIn, type OperatorClientDetail, type OperatorReport } from "@/lib/types-operator";

export default function OperatorClientPage() {
  const user = useSession();
  const { id } = useParams<{ id: string }>();
  return (
    <Shell user={user}>
      {user && (user.is_operator ? <ClientView id={id} /> : <p className="text-slate-500">The operator console is for registered operators only.</p>)}
    </Shell>
  );
}

function ClientView({ id }: { id: string }) {
  const [client, setClient] = useState<OperatorClientDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [claim, setClaim] = useState<ClaimTokenOut | null>(null);

  const load = useCallback(async () => {
    try {
      setClient(await api<OperatorClientDetail>(`/operator/clients/${id}`));
      setError(null);
    } catch (e) {
      setError(errText(e));
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  async function withdraw() {
    if (!window.confirm("Withdraw this client's consent? Their details can no longer be used and the stored name is erased.")) return;
    try {
      await api(`/operator/clients/${id}/consent/withdraw`, { method: "POST" });
      await load();
    } catch (e) {
      setError(errText(e));
    }
  }

  async function regrant(consent: ConsentIn) {
    try {
      await api(`/operator/clients/${id}/consent`, { method: "POST", body: consent });
      await load();
    } catch (e) {
      setError(errText(e));
    }
  }

  async function issueClaim() {
    try {
      setClaim(await api<ClaimTokenOut>(`/operator/clients/${id}/claim-token`, { method: "POST" }));
    } catch (e) {
      setError(errText(e));
    }
  }

  return (
    <div className="space-y-6">
      <OperatorNav />
      <ErrorNote error={error} />
      {client && (
        <>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-xl font-semibold">{client.full_name || "(no name stored)"}</h1>
            <span className="text-sm text-slate-500">added {fmtDate(client.created_at)}</span>
            {client.claimed && <Badge value="CLAIMED" />}
            <Link href={`/operator/reports/new?client=${client.id}`} className="ml-auto rounded bg-blue-700 px-3 py-1 text-sm text-white">
              New report
            </Link>
          </div>

          <section className="space-y-2 rounded border border-slate-200 bg-white p-4 text-sm">
            <h2 className="font-semibold">Consent</h2>
            {client.consent ? (
              <>
                <p>
                  Recorded {fmtDate(client.consent.granted_at, true)} ({client.consent.method.replace(/_/g, " ").toLowerCase()}, notice{" "}
                  {client.consent.notice_version}){client.consent.guardian_name && <> with guardian {client.consent.guardian_name}</>}.
                </p>
                <p>Items: {client.consent.data_items.join(", ")}</p>
                <button className="rounded border border-red-300 px-2 py-0.5 text-xs text-red-800" onClick={withdraw}>
                  Withdraw consent
                </button>
              </>
            ) : (
              <>
                <p className="text-amber-800">No active consent: nothing about this client can be entered or used until the client agrees again.</p>
                <ConsentForm submitLabel="Record consent" onSubmit={regrant} />
              </>
            )}
          </section>

          {client.consent && <ProfileForm clientId={client.id} items={client.consent.data_items} onSaved={load} versions={client.profile_versions.length} />}

          <section className="space-y-2 rounded border border-slate-200 bg-white p-4 text-sm">
            <h2 className="font-semibold">Client account</h2>
            {client.claimed ? (
              <p>The client has set their own email and password and can log in.</p>
            ) : (
              <>
                <p>Give the client a one-time code so they can take over this account at /claim with their own email and password.</p>
                <button className="rounded border border-slate-300 px-2 py-0.5 text-xs" onClick={issueClaim}>
                  Make claim code
                </button>
                {claim && (
                  <p className="rounded bg-slate-50 p-2">
                    <span className="font-mono">{claim.token}</span>
                    <br />
                    <span className="text-xs text-slate-500">
                      Valid until {fmtDate(claim.expires_at, true)}. {claim.note}
                    </span>
                  </p>
                )}
              </>
            )}
          </section>

          <section className="space-y-2">
            <h2 className="font-semibold">Reports</h2>
            {client.reports.length === 0 && <p className="text-sm text-slate-500">No reports yet.</p>}
            {client.reports.map((r) => (
              <ReportCard key={r.id} report={r} />
            ))}
          </section>
        </>
      )}
    </div>
  );
}

const CATEGORIES = ["", "UR", "OBC", "SC", "ST", "EWS"];

function ProfileForm({ clientId, items, versions, onSaved }: { clientId: string; items: string[]; versions: number; onSaved: () => void }) {
  const [info, setInfo] = useState<PersonalInfoFull>({});
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  useEffect(() => {
    api<{ personal_info: PersonalInfoFull | null }>(`/operator/clients/${clientId}/profile`)
      .then((p) => setInfo(p.personal_info ?? {}))
      .catch(() => setInfo({})); // 404 until the first save
  }, [clientId, versions]);

  if (!items.includes("personal_info")) {
    return <p className="text-sm text-slate-500">The client did not consent to personal details; the profile cannot be edited here.</p>;
  }

  async function save(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const body = {
        personal_info: {
          full_name: info.full_name || null,
          date_of_birth: info.date_of_birth || null,
          gender: info.gender || null,
          category: info.category || null,
          is_pwbd: info.is_pwbd ?? null,
          contact: info.contact || null,
        },
      };
      const out = await api<{ version: { version_number: number } }>(`/operator/clients/${clientId}/profile`, { method: "PATCH", body });
      setSaved(`Saved as version ${out.version.version_number}`);
      onSaved();
    } catch (err) {
      setError(errText(err));
    }
  }

  const set = (k: keyof PersonalInfoFull, v: string | boolean | null) => setInfo({ ...info, [k]: v });
  return (
    <form onSubmit={save} className="grid gap-2 rounded border border-slate-200 bg-white p-4 text-sm sm:grid-cols-2">
      <h2 className="font-semibold sm:col-span-2">Basic details ({versions} version{versions === 1 ? "" : "s"})</h2>
      <label>
        Full name as in certificates
        <input className="mt-1 block w-full rounded border border-slate-300 px-2 py-1" value={info.full_name ?? ""} onChange={(e) => set("full_name", e.target.value)} />
      </label>
      <label>
        Date of birth
        <input type="date" className="mt-1 block w-full rounded border border-slate-300 px-2 py-1" value={info.date_of_birth ?? ""} onChange={(e) => set("date_of_birth", e.target.value)} />
      </label>
      <label>
        Gender
        <select className="mt-1 block w-full rounded border border-slate-300 px-2 py-1" value={info.gender ?? ""} onChange={(e) => set("gender", e.target.value)}>
          {["", "Female", "Male", "Transgender"].map((g) => (
            <option key={g} value={g}>
              {g || "not stated"}
            </option>
          ))}
        </select>
      </label>
      <label>
        Category
        <select className="mt-1 block w-full rounded border border-slate-300 px-2 py-1" value={info.category ?? ""} onChange={(e) => set("category", e.target.value)}>
          {CATEGORIES.map((c) => (
            <option key={c} value={c}>
              {c || "not stated"}
            </option>
          ))}
        </select>
      </label>
      <label>
        Person with benchmark disability
        <select
          className="mt-1 block w-full rounded border border-slate-300 px-2 py-1"
          value={info.is_pwbd == null ? "" : info.is_pwbd ? "yes" : "no"}
          onChange={(e) => set("is_pwbd", e.target.value === "" ? null : e.target.value === "yes")}
        >
          <option value="">not stated</option>
          <option value="yes">yes</option>
          <option value="no">no</option>
        </select>
      </label>
      <label>
        Contact
        <input className="mt-1 block w-full rounded border border-slate-300 px-2 py-1" value={info.contact ?? ""} onChange={(e) => set("contact", e.target.value)} />
      </label>
      <div className="flex items-center gap-3 sm:col-span-2">
        <button className="rounded bg-blue-700 px-3 py-1 text-white">Save new version</button>
        {saved && <span className="text-slate-500">{saved}</span>}
      </div>
      <div className="sm:col-span-2">
        <ErrorNote error={error} />
      </div>
    </form>
  );
}

function ReportCard({ report }: { report: OperatorReport }) {
  const s = report.snapshot;
  const [checklist, setChecklist] = useState<PrepChecklist | null>(null);
  const [error, setError] = useState<string | null>(null);
  const doc = safeHref(s.document_url);

  async function showChecklist() {
    try {
      setChecklist(await api<PrepChecklist>(`/operator/reports/${report.id}/checklist`));
    } catch (e) {
      setError(errText(e));
    }
  }

  return (
    <div className="space-y-2 rounded border border-slate-200 bg-white p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{s.title}</span>
        <span className="text-slate-500">{s.organization}</span>
        {s.advertisement_number && <span className="text-slate-500">Advt. {s.advertisement_number}</span>}
        <span className="ml-auto text-xs text-slate-500">{fmtDate(report.created_at, true)}</span>
      </div>
      <div className="flex flex-wrap gap-3 text-xs">
        <span>Last date: {fmtDate(s.deadline, true)}</span>
        <a className="text-blue-700 hover:underline" href={`${API_URL}${s.links.html}`} target="_blank" rel="noreferrer">
          Printable apply-ready page
        </a>
        {doc && (
          <a className="text-blue-700 hover:underline" href={doc} target="_blank" rel="noreferrer">
            Official notification
          </a>
        )}
        {report.charge && (
          <span className="rounded bg-amber-100 px-1 text-amber-800">
            {rupees(report.charge.amount_paise)} TEST MODE ({report.charge.provider})
          </span>
        )}
        {report.profile_version_id && (
          <button className="text-blue-700 hover:underline" onClick={showChecklist}>
            Client checklist
          </button>
        )}
      </div>
      <ErrorNote error={error} />
      {checklist && (
        <ul className="space-y-1 border-t border-slate-100 pt-2 text-xs">
          {checklist.fee && (
            <li>
              Fee: <Badge value={checklist.fee.status} /> {checklist.fee.reason}
            </li>
          )}
          {checklist.documents.map((d) => (
            <li key={d.code}>
              <Badge value={d.status} /> {d.code.replace(/_/g, " ").toLowerCase()}: {d.reason}
              {d.citation.page != null && <span className="text-slate-500"> (notice p. {d.citation.page})</span>}
            </li>
          ))}
          {checklist.warnings.map((w) => (
            <li key={w} className="text-amber-800">
              {w}
            </li>
          ))}
        </ul>
      )}
      <p className="text-xs text-slate-500">{s.disclaimer}</p>
    </div>
  );
}
