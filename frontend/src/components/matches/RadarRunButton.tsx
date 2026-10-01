"use client";
/**
 * "Run radar now": POST /radar/run re-checks the caller's latest profile version against all open
 * vacancies and creates alerts. Shows how many evaluations ran and which alerts were created, or the
 * API's error message. onDone lets the page reload its matches afterwards.
 */
import Link from "next/link";
import { useState } from "react";

import { ErrorNote, errText } from "@/components/ui";
import { api } from "@/lib/api";
import type { RadarRunResult } from "@/lib/types-matches-relevance";

export function RadarRunButton({ onDone }: { onDone?: () => void }) {
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<RadarRunResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      setResult(await api<RadarRunResult>("/radar/run", { method: "POST" }));
      onDone?.();
    } catch (e) {
      setError(`Radar run failed: ${errText(e)}`);
    } finally {
      setRunning(false);
    }
  }

  const created = result ? Object.entries(result.alerts_created).filter(([, n]) => n > 0) : [];
  const totalAlerts = created.reduce((s, [, n]) => s + n, 0);

  return (
    <div>
      <button
        onClick={run}
        disabled={running}
        className="rounded bg-blue-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-800 disabled:cursor-wait disabled:opacity-60"
      >
        {running ? "Running radar…" : "Run radar now"}
      </button>
      <ErrorNote error={error} />
      {result && (
        <p className="mt-2 rounded border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-900" role="status">
          Radar checked {result.evaluations} vacanc{result.evaluations === 1 ? "y" : "ies"}.{" "}
          {totalAlerts === 0 ? (
            "No new alerts."
          ) : (
            <>
              {totalAlerts} new alert{totalAlerts === 1 ? "" : "s"} (
              {created.map(([kind, n]) => `${n} ${kind.toLowerCase().replace(/_/g, " ")}`).join(", ")}) ·{" "}
              <Link href="/alerts" className="underline">
                view alerts
              </Link>
            </>
          )}
        </p>
      )}
    </div>
  );
}
