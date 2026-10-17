"use client";
/**
 * Deputation vacancy circulars parsed from stored official PDFs (GET /deputation/circulars): newest closing date
 * first, circulars whose closing depends on an Employment News issue last. "Open only" asks the backend for
 * circulars whose closing date has not passed.
 */
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { termValue } from "@/components/deputation";
import { Badge, ErrorNote, Shell, errText, fmtDate, useSession } from "@/components/ui";
import { api } from "@/lib/api";
import type { CircularSummary } from "@/lib/types-deputation";

const PAGE = 50;

export default function DeputationPage() {
  const user = useSession();
  return <Shell user={user}>{user && <Circulars />}</Shell>;
}

function closingText(c: CircularSummary): string {
  if (c.closing_date) return fmtDate(c.closing_date);
  if (c.closing_days) return `${c.closing_days} days from Employment News publication`;
  return c.closing ? termValue(c.closing.value) : "not stated";
}

function Circulars() {
  const [openOnly, setOpenOnly] = useState(false);
  const [offset, setOffset] = useState(0);
  const [rows, setRows] = useState<CircularSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRows(await api<CircularSummary[]>(`/deputation/circulars?open_only=${openOnly}&limit=${PAGE}&offset=${offset}`));
      setError(null);
    } catch (e) {
      setError(errText(e));
    }
  }, [openOnly, offset]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">Deputation circulars</h1>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={openOnly}
            onChange={(e) => {
              setOpenOnly(e.target.checked);
              setOffset(0);
            }}
          />
          Open only
        </label>
      </div>
      <p className="text-sm text-slate-600">
        For serving government employees. Each circular&apos;s terms are read from the official PDF with their page and words; the
        official circular prevails.
      </p>
      <ErrorNote error={error} />
      {rows === null && !error && <p className="text-slate-500">Loading…</p>}
      {rows?.length === 0 && <p className="text-sm text-slate-500">No deputation circulars{openOnly ? " open now" : ""}.</p>}
      {rows && rows.length > 0 && (
        <div className="overflow-x-auto rounded border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="bg-slate-100 text-left">
              <tr>
                <th className="px-3 py-2">Circular</th>
                <th className="px-3 py-2">Posts</th>
                <th className="px-3 py-2">Mode</th>
                <th className="px-3 py-2">Closing</th>
                <th className="px-3 py-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.id} className="border-t border-slate-100 align-top">
                  <td className="px-3 py-2">
                    <Link href={`/deputation/${c.id}`} className="font-medium text-blue-700 hover:underline">
                      {c.title || c.attribution?.organization || "Deputation circular"}
                    </Link>
                    {c.attribution && <div className="text-xs text-slate-500">{c.attribution.source_name}</div>}
                  </td>
                  <td className="px-3 py-2">
                    {c.post_count}
                    {c.posts && c.posts.length > 0 && (
                      <div className="text-xs text-slate-500">
                        {c.posts
                          .slice(0, 3)
                          .map((p) => termValue(p.name.value))
                          .join("; ")}
                        {c.posts.length > 3 ? " …" : ""}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2 text-xs">{c.mode?.length ? c.mode.join(", ").replace(/_/g, " ") : "—"}</td>
                  <td className="px-3 py-2">{closingText(c)}</td>
                  <td className="px-3 py-2">{c.status ? <Badge value={c.status} /> : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="flex gap-3 text-sm">
        <button className="text-blue-700 underline disabled:text-slate-400 disabled:no-underline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
          Previous
        </button>
        <button className="text-blue-700 underline disabled:text-slate-400 disabled:no-underline" disabled={(rows?.length ?? 0) < PAGE} onClick={() => setOffset(offset + PAGE)}>
          Next
        </button>
      </div>
    </div>
  );
}
