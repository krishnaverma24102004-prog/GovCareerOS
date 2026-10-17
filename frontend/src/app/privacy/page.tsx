"use client";
/**
 * Privacy (DPDP Act 2023): consents given and withdrawn, give a new one, download all my data, erase my account.
 * Correction is done by editing the profile. Withdrawal stops use for that purpose; it does not delete anything.
 */
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { Dialog } from "@/components/Dialog";
import { ConsentForm } from "@/components/privacy/ConsentForm";
import { Badge, ErrorNote, Shell, errText, fmtDate, useSession } from "@/components/ui";
import { api, setToken } from "@/lib/api";
import type { Consents, EraseResult } from "@/lib/types-privacy";

export default function PrivacyPage() {
  const user = useSession();
  return <Shell user={user}>{user && <PrivacyWorkspace />}</Shell>;
}

function PrivacyWorkspace() {
  const router = useRouter();
  const [consents, setConsents] = useState<Consents | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [confirmErase, setConfirmErase] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setConsents(await api<Consents>("/privacy/consents"));
    } catch (e) {
      setError(errText(e));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function withdraw(purpose: string) {
    setError(null);
    try {
      await api(`/privacy/consents/${encodeURIComponent(purpose)}`, { method: "DELETE" });
      setNote(`Consent for ${purpose.replace(/_/g, " ")} withdrawn. Your stored data is unchanged; erase your account to delete it.`);
      await load();
    } catch (e) {
      setError(errText(e));
    }
  }

  async function exportData() {
    setError(null);
    try {
      const data = await api<unknown>("/privacy/export");
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = `govcareeros-my-data-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(errText(e));
    }
  }

  async function erase() {
    setBusy(true);
    setError(null);
    try {
      const r = await api<EraseResult>("/privacy/erase", { method: "POST", body: { confirm: "ERASE" } });
      setToken(null);
      window.alert(r.message);
      router.replace("/login");
    } catch (e) {
      setError(errText(e));
      setBusy(false);
    }
  }

  const records = consents?.records ?? [];
  return (
    <div className="max-w-3xl space-y-6">
      <h1 className="text-xl font-semibold">Privacy</h1>
      <ErrorNote error={error} />
      {note && <p className="rounded border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-800">{note}</p>}

      <section className="space-y-2">
        <h2 className="font-medium">Consents</h2>
        {consents && consents.outdated.length > 0 && (
          <p className="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
            The notice has changed since you consented to {consents.outdated.join(", ")}. Please read it again below.
          </p>
        )}
        {records.length === 0 ? (
          <p className="text-sm text-slate-500">No consent given yet.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-slate-500">
              <tr>
                <th className="py-1">Purpose</th>
                <th>Given</th>
                <th>Notice</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {records.map((r) => (
                <tr key={r.id} className="border-t border-slate-100">
                  <td className="py-1">
                    {r.purpose.replace(/_/g, " ")}
                    {r.guardian_name && <span className="block text-xs text-slate-500">by guardian {r.guardian_name}</span>}
                  </td>
                  <td>{fmtDate(r.granted_at, true)}</td>
                  <td>{r.notice_version}</td>
                  <td>{r.withdrawn_at ? <span className="text-slate-500">withdrawn {fmtDate(r.withdrawn_at)}</span> : <Badge value="ACTIVE" />}</td>
                  <td className="text-right">
                    {!r.withdrawn_at && (
                      <button type="button" onClick={() => withdraw(r.purpose)} className="text-red-700 underline">
                        Withdraw
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="space-y-2">
        <h2 className="font-medium">Give consent</h2>
        {consents && (
          <ConsentForm
            key={consents.active.join(",")}
            active={consents.active.filter((p) => !consents.outdated.includes(p))}
            onDone={() => {
              setNote("Consent recorded.");
              load();
            }}
          />
        )}
      </section>

      <section className="space-y-2">
        <h2 className="font-medium">Your data</h2>
        <p className="text-sm text-slate-600">
          Download everything we hold about you as a JSON file. To correct something, edit your{" "}
          <Link href="/profile" className="underline">
            profile
          </Link>
          .
        </p>
        <button type="button" onClick={exportData} className="rounded border border-slate-300 px-3 py-1 text-sm hover:bg-slate-100">
          Download my data
        </button>
      </section>

      <section className="space-y-2">
        <h2 className="font-medium">Erase my account</h2>
        <p className="text-sm text-slate-600">
          Your account closes at once and you are logged out everywhere. Your data is permanently erased after a grace period,
          except records the law requires us to keep (proof of your consent, and data under a legal hold).
        </p>
        <button type="button" onClick={() => setConfirmErase(true)} className="rounded bg-red-600 px-3 py-1 text-sm text-white">
          Erase my account…
        </button>
      </section>

      {confirmErase && (
        <Dialog title="Erase my account" onClose={() => setConfirmErase(false)}>
          <p className="text-sm">
            Type <strong>ERASE</strong> to close your account and schedule the erasure of your data.
          </p>
          <input className="w-full rounded border border-slate-300 px-3 py-2" value={typed} onChange={(e) => setTyped(e.target.value)} />
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => setConfirmErase(false)} className="rounded px-3 py-1 text-sm">
              Cancel
            </button>
            <button
              type="button"
              disabled={typed !== "ERASE" || busy}
              onClick={erase}
              className="rounded bg-red-600 px-3 py-1 text-sm text-white disabled:opacity-50"
            >
              Erase
            </button>
          </div>
        </Dialog>
      )}
    </div>
  );
}
