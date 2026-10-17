"use client";
/**
 * Operator console home (cyber-cafe / CSC operators): the operator's own clients and the "new client" form.
 *
 * A client is created only together with their consent: the operator reads the consent notice out to the client
 * (GET /operator/consent-notice), ticks the items the client agrees to and confirms the client is present. The
 * backend stores nothing about the client before that consent is recorded.
 */
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Badge, ErrorNote, Shell, errText, fmtDate, useSession } from "@/components/ui";
import { ConsentForm, OperatorNav } from "@/components/operator";
import { api } from "@/lib/api";
import type { ConsentIn, OperatorClient } from "@/lib/types-operator";

export default function OperatorHome() {
  const user = useSession();
  return (
    <Shell user={user}>
      {user && (user.is_operator ? <Clients /> : <p className="text-slate-500">The operator console is for registered operators only.</p>)}
    </Shell>
  );
}

function Clients() {
  const [clients, setClients] = useState<OperatorClient[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");

  const load = useCallback(async () => {
    try {
      setClients(await api<OperatorClient[]>("/operator/clients"));
    } catch (e) {
      setError(errText(e));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function create(consent: ConsentIn) {
    setError(null);
    try {
      await api<OperatorClient>("/operator/clients", { method: "POST", body: { full_name: name.trim() || null, consent } });
      setName("");
      setAdding(false);
      await load();
    } catch (e) {
      setError(errText(e));
    }
  }

  return (
    <div className="space-y-4">
      <OperatorNav />
      <div className="flex items-center gap-3">
        <h1 className="text-xl font-semibold">Clients</h1>
        <span className="text-sm text-slate-500">{clients?.length ?? 0}</span>
        <button className="ml-auto rounded bg-blue-700 px-3 py-1 text-sm text-white" onClick={() => setAdding(!adding)}>
          {adding ? "Cancel" : "New client"}
        </button>
      </div>
      <ErrorNote error={error} />
      {adding && (
        <section className="space-y-3 rounded border border-slate-200 bg-white p-4">
          <label className="block text-sm">
            Client name / Naam
            <input
              className="mt-1 block w-full rounded border border-slate-300 px-2 py-1"
              value={name}
              maxLength={255}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <ConsentForm submitLabel="Record consent and create client" onSubmit={create} />
        </section>
      )}
      <table className="w-full rounded border border-slate-200 bg-white text-sm">
        <thead className="bg-slate-50 text-left text-xs text-slate-500">
          <tr>
            <th className="px-3 py-1">Name</th>
            <th className="px-3 py-1">Added</th>
            <th className="px-3 py-1">Consent</th>
            <th className="px-3 py-1">Account</th>
          </tr>
        </thead>
        <tbody>
          {clients?.map((c) => (
            <tr key={c.id} className="border-t border-slate-100">
              <td className="px-3 py-1">
                <Link className="text-blue-700 hover:underline" href={`/operator/clients/${c.id}`}>
                  {c.full_name || <span className="text-slate-500">(no name stored) {c.id.slice(0, 8)}</span>}
                </Link>
              </td>
              <td className="px-3 py-1">{fmtDate(c.created_at)}</td>
              <td className="px-3 py-1">{c.consent ? <Badge value="ACTIVE" /> : <Badge value="WITHDRAWN" />}</td>
              <td className="px-3 py-1">{c.claimed ? "claimed by client" : "managed"}</td>
            </tr>
          ))}
          {clients?.length === 0 && (
            <tr>
              <td colSpan={4} className="px-3 py-3 text-slate-500">
                No clients yet.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
