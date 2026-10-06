"use client";
/**
 * The operator's monthly statement (IST calendar month): every report charged that month and the total.
 * TEST MODE only: the ledger is written through a fake provider; nothing is collected.
 */
import Link from "next/link";
import { useEffect, useState } from "react";

import { ErrorNote, Shell, errText, fmtDate, useSession } from "@/components/ui";
import { OperatorNav } from "@/components/operator";
import { api } from "@/lib/api";
import { rupees, type Statement } from "@/lib/types-operator";

export default function StatementPage() {
  const user = useSession();
  return (
    <Shell user={user}>
      {user && (user.is_operator ? <StatementView /> : <p className="text-slate-500">The operator console is for registered operators only.</p>)}
    </Shell>
  );
}

function thisMonth(): string {
  // IST month, as the backend counts it
  const ist = new Date(Date.now() + 330 * 60 * 1000);
  return ist.toISOString().slice(0, 7);
}

function StatementView() {
  const [month, setMonth] = useState(thisMonth);
  const [statement, setStatement] = useState<Statement | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!/^\d{4}-\d{2}$/.test(month)) return;
    api<Statement>(`/operator/statement?month=${month}`)
      .then((s) => {
        setStatement(s);
        setError(null);
      })
      .catch((e) => setError(errText(e)));
  }, [month]);

  return (
    <div className="space-y-4">
      <OperatorNav />
      <div className="flex items-center gap-3">
        <h1 className="text-xl font-semibold">Statement</h1>
        <input type="month" className="rounded border border-slate-300 px-2 py-1 text-sm" value={month} onChange={(e) => setMonth(e.target.value)} />
      </div>
      <ErrorNote error={error} />
      {statement && (
        <>
          <p className="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">{statement.note}</p>
          <p className="text-sm">
            {statement.report_count} report{statement.report_count === 1 ? "" : "s"}, total <span className="font-semibold">{rupees(statement.total_paise)}</span>
          </p>
          <table className="w-full rounded border border-slate-200 bg-white text-sm">
            <thead className="bg-slate-50 text-left text-xs text-slate-500">
              <tr>
                <th className="px-3 py-1">When</th>
                <th className="px-3 py-1">Client</th>
                <th className="px-3 py-1">Report</th>
                <th className="px-3 py-1 text-right">Amount</th>
                <th className="px-3 py-1">Reference</th>
              </tr>
            </thead>
            <tbody>
              {statement.entries.map((e) => (
                <tr key={e.entry_id} className="border-t border-slate-100">
                  <td className="whitespace-nowrap px-3 py-1 text-xs">{fmtDate(e.at, true)}</td>
                  <td className="px-3 py-1">
                    <Link className="text-blue-700 hover:underline" href={`/operator/clients/${e.client_id}`}>
                      {e.client_name || e.client_id.slice(0, 8)}
                    </Link>
                  </td>
                  <td className="px-3 py-1">{e.title}</td>
                  <td className="px-3 py-1 text-right">{rupees(e.amount_paise)}</td>
                  <td className="px-3 py-1 font-mono text-xs text-slate-500">
                    {e.provider} {e.provider_ref}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
