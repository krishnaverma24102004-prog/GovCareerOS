"use client";
/**
 * Examinations (SSC CHSL, ...) as the backend stores them from official notices: conducting body,
 * application window and notice status. The window label ("open", "closed", ...) is computed here
 * from the stored dates only; the status badge is exactly what the backend holds.
 */
import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";

import { Badge, ErrorNote, Shell, errText, fmtDate, useSession } from "@/components/ui";
import { api } from "@/lib/api";
import type { ExamSummary } from "@/lib/types-exams";

const PAGE_SIZE = 50;

export default function ExamsPage() {
  const user = useSession();
  return <Shell user={user}>{user && <ExamsView />}</Shell>;
}

/** Where today falls in the application window, from the stored dates (null when the notice gave none). */
function windowState(e: Pick<ExamSummary, "status" | "application_start" | "application_end">): string | null {
  if (e.status === "CANCELLED") return null;
  const now = Date.now();
  const start = e.application_start ? new Date(e.application_start).getTime() : NaN;
  const end = e.application_end ? new Date(e.application_end).getTime() : NaN;
  if (!isNaN(start) && now < start) return "opens soon";
  if (!isNaN(end)) {
    if (now > end) return "closed";
    const days = Math.ceil((end - now) / 86_400_000);
    return days <= 1 ? "open · closes within a day" : `open · ${days} days left`;
  }
  return null;
}

function ExamsView() {
  const [exams, setExams] = useState<ExamSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [q, setQ] = useState("");
  const [openOnly, setOpenOnly] = useState(false);
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    let live = true;
    setExams(null);
    setError(null);
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset), open_only: String(openOnly) });
    if (q) params.set("q", q);
    api<ExamSummary[]>(`/exams?${params}`)
      .then((rows) => live && setExams(rows))
      .catch((e) => live && setError(errText(e)));
    return () => {
      live = false;
    };
  }, [q, openOnly, offset]);

  function search(ev: FormEvent) {
    ev.preventDefault();
    setOffset(0);
    setQ(draft.trim());
  }

  const filtered = Boolean(q) || openOnly;

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-4">
        <h1 className="text-xl font-semibold">Exams</h1>
        <form onSubmit={search} className="flex items-center gap-2">
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Exam name or code"
            aria-label="Search exams by name or code"
            className="rounded border border-slate-300 px-2 py-1 text-sm"
          />
          <button type="submit" className="rounded bg-slate-800 px-3 py-1 text-sm text-white hover:bg-slate-700">
            Search
          </button>
          {q && (
            <button
              type="button"
              onClick={() => {
                setDraft("");
                setQ("");
                setOffset(0);
              }}
              className="text-sm text-slate-600 hover:text-slate-900"
            >
              Clear
            </button>
          )}
        </form>
        <label className="ml-auto flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={openOnly}
            onChange={(e) => {
              setOpenOnly(e.target.checked);
              setOffset(0);
            }}
          />
          Only open for applications
        </label>
      </div>

      <ErrorNote error={error} />
      {exams === null && !error && <p className="text-slate-500">Loading…</p>}
      {exams?.length === 0 && (
        <p className="text-slate-500">
          {offset > 0
            ? "No more exams."
            : filtered
              ? "No exams match these filters."
              : "No exams in the database yet. They appear once an official exam notice has been ingested."}
        </p>
      )}
      {exams && exams.length > 0 && (
        <div className="overflow-x-auto rounded border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="bg-slate-100 text-left">
              <tr>
                <th className="px-3 py-2">Exam</th>
                <th className="px-3 py-2">Conducting body</th>
                <th className="px-3 py-2">Application window</th>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2 text-right">Vacancies</th>
              </tr>
            </thead>
            <tbody>
              {exams.map((e) => {
                const state = windowState(e);
                return (
                  <tr key={e.id} className="border-t border-slate-100 align-top">
                    <td className="px-3 py-2">
                      <Link className="font-medium text-blue-700 hover:underline" href={`/exams/${e.id}`}>
                        {e.name}
                      </Link>
                      <div className="text-xs text-slate-500">
                        {[e.exam_code, e.exam_year].filter((x) => x !== null && x !== "").join(" · ") || null}
                      </div>
                    </td>
                    <td className="px-3 py-2">{e.conducting_body}</td>
                    <td className="whitespace-nowrap px-3 py-2">
                      {fmtDate(e.application_start)} – {fmtDate(e.application_end)}
                      {state && <div className="text-xs text-slate-500">{state}</div>}
                    </td>
                    <td className="px-3 py-2">
                      <Badge value={e.status} />
                    </td>
                    <td className="px-3 py-2 text-right">
                      {e.tentative_vacancies ?? "—"}
                      {e.tentative_vacancies !== null && <div className="text-xs text-slate-500">tentative</div>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {exams && (offset > 0 || exams.length === PAGE_SIZE) && (
        <div className="mt-3 flex items-center gap-3 text-sm">
          <button
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            className="rounded border border-slate-300 px-3 py-1 disabled:opacity-40"
          >
            Previous
          </button>
          {exams.length > 0 && (
            <span className="text-slate-500">
              {offset + 1}–{offset + exams.length}
            </span>
          )}
          <button
            disabled={exams.length < PAGE_SIZE}
            onClick={() => setOffset(offset + PAGE_SIZE)}
            className="rounded border border-slate-300 px-3 py-1 disabled:opacity-40"
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
