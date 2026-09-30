"use client";
/** The caller's alerts (unread first, most urgent first, as the backend orders them); mark one or all read. */
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Badge, ErrorNote, Shell, errText, fmtDate, useSession } from "@/components/ui";
import { api } from "@/lib/api";
import type { Alert } from "@/lib/types";

export default function AlertsPage() {
  const user = useSession();
  return <Shell user={user}>{user && <AlertsView />}</Shell>;
}

function AlertsView() {
  const [alerts, setAlerts] = useState<Alert[] | null>(null);
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setAlerts(await api<Alert[]>(`/alerts?unread_only=${unreadOnly}`));
    } catch (e) {
      setError(errText(e));
    }
  }, [unreadOnly]);

  useEffect(() => {
    load();
  }, [load]);

  async function markRead(ids: string[]) {
    setError(null);
    try {
      for (const id of ids) await api(`/alerts/${id}/read`, { method: "POST" });
    } catch (e) {
      setError(errText(e));
    }
    await load();
  }

  const unread = alerts?.filter((a) => !a.read_at) ?? [];
  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-4">
        <h1 className="text-xl font-semibold">Alerts</h1>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={unreadOnly} onChange={(e) => setUnreadOnly(e.target.checked)} />
          Unread only
        </label>
        {unread.length > 0 && (
          <button onClick={() => markRead(unread.map((a) => a.id))} className="ml-auto text-sm text-blue-700 underline">
            Mark all {unread.length} read
          </button>
        )}
      </div>
      <ErrorNote error={error} />
      {alerts === null && !error && <p className="text-slate-500">Loading…</p>}
      {alerts?.length === 0 && <p className="text-slate-500">No alerts.</p>}
      <ul className="space-y-2">
        {alerts?.map((a) => (
          <li key={a.id} className={`rounded border p-3 text-sm ${a.read_at ? "border-slate-200 bg-slate-50 text-slate-600" : "border-blue-200 bg-white"}`}>
            <div className="flex flex-wrap items-center gap-2">
              <Badge value={a.kind} />
              <span className="font-medium">{a.title}</span>
              {a.due_at && <span className="text-xs text-slate-500">due {fmtDate(a.due_at, true)}</span>}
              <span className="ml-auto text-xs text-slate-500">{fmtDate(a.created_at, true)}</span>
            </div>
            <p className="mt-1 whitespace-pre-wrap">{a.body}</p>
            <div className="mt-2 flex gap-4 text-xs">
              {a.vacancy_id && (
                <Link className="text-blue-700 underline" href={`/vacancies/${a.vacancy_id}`}>
                  Open vacancy
                </Link>
              )}
              {!a.read_at && (
                <button onClick={() => markRead([a.id])} className="text-blue-700 underline">
                  Mark read
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
