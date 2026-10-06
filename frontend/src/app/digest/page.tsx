"use client";
/**
 * The caller's latest weekly digest (GET /digest/latest?format=json), the same content the digest email carries.
 * Official changes to what the user applied for or is eligible for come first: each corrigendum's lines with the
 * earlier value's and the new value's citation. Then matches, deadlines, age headroom and notices.
 *
 * "Build my digest now" calls POST /digest/generate, which stores a digest since the previous one; it never
 * sends an email (delivery is the scripts/send_digests.py --send job).
 */
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { pageLink } from "@/components/applications";
import { Badge, ErrorNote, Shell, errText, fmtDate, useSession } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { DIGEST_SECTIONS, type DigestCitation, type DigestItem, type LatestDigest } from "@/lib/types-changes";

export default function DigestPage() {
  const user = useSession();
  return <Shell user={user}>{user && <DigestView />}</Shell>;
}

function DigestView() {
  const [latest, setLatest] = useState<LatestDigest | null>(null);
  const [none, setNone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [building, setBuilding] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      setLatest(await api<LatestDigest>("/digest/latest?format=json"));
      setNone(false);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setNone(true);
      else setError(errText(e));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function build() {
    setBuilding(true);
    setError(null);
    try {
      await api("/digest/generate", { method: "POST" });
      await load();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBuilding(false);
    }
  }

  const d = latest?.digest;
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">Digest</h1>
        {latest && (
          <span className="text-sm text-slate-500">
            {fmtDate(latest.run.period_start)} – {fmtDate(latest.run.period_end)}
            {d?.profile_version_number ? ` · profile version ${d.profile_version_number}` : ""}
            {latest.run.sent_at ? ` · emailed ${fmtDate(latest.run.sent_at, true)}` : " · not emailed"}
          </span>
        )}
        <button
          onClick={build}
          disabled={building}
          className="ml-auto rounded bg-blue-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-800 disabled:opacity-50"
        >
          {building ? "Building…" : "Build my digest now"}
        </button>
      </div>
      <ErrorNote error={error} />
      {none && <p className="text-sm text-slate-600">No digest yet. Build one to see what changed since your last digest.</p>}
      {!latest && !none && !error && <p className="text-slate-500">Loading…</p>}

      {d &&
        DIGEST_SECTIONS.map(([key, heading]) => {
          const items = d[key] ?? [];
          return (
            <section key={key}>
              <h2 className="mb-2 font-medium">
                {heading} <span className="text-sm font-normal text-slate-500">({items.length})</span>
              </h2>
              {items.length ? (
                <ul className="space-y-3">
                  {items.map((it, i) => (
                    <DigestCard key={`${key}-${i}`} it={it} />
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-slate-500">Nothing in this period.</p>
              )}
            </section>
          );
        })}

      {d && d.omitted.length > 0 && (
        <section>
          <h2 className="mb-2 font-medium text-slate-600">Left out for want of a citation ({d.omitted.length})</h2>
          <ul className="list-disc pl-5 text-sm text-slate-600">
            {d.omitted.map((o, i) => (
              <li key={i}>{o}</li>
            ))}
          </ul>
        </section>
      )}
      {d && <p className="text-xs text-slate-500">Information from official notifications; the official notification prevails.</p>}
    </div>
  );
}

function DigestCard({ it }: { it: DigestItem }) {
  const href = it.exam_id ? `/exams/${it.exam_id}` : it.vacancy_id ? `/vacancies/${it.vacancy_id}` : null;
  // A change item's first detail line names the notice; the rest are one line per changed field
  const [head, ...rest] = it.kind === "CHANGE" ? it.details : [null, ...it.details];
  return (
    <li className="rounded border border-slate-200 bg-white p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        {href ? (
          <Link href={href} className="font-medium text-blue-700 hover:underline">
            {it.headline}
          </Link>
        ) : (
          <span className="font-medium">{it.headline}</span>
        )}
        {it.status && <Badge value={it.status} />}
        {it.due_at && <span className="ml-auto text-xs text-slate-500">last date {fmtDate(it.due_at, true)}</span>}
      </div>
      <p className="text-xs text-slate-500">{[it.organization, it.advertisement && `Advt ${it.advertisement}`].filter(Boolean).join(" · ")}</p>
      {head && <p className="mt-1 text-slate-600">{head}</p>}
      {rest.length > 0 && (
        <ul className="mt-1 list-disc pl-5">
          {rest.map((line, i) => (
            <li key={i}>{line}</li>
          ))}
        </ul>
      )}
      {it.open_checks.length > 0 && (
        <ul className="mt-2 space-y-1">
          {it.open_checks.map((c, i) => (
            <li key={i}>
              <span className="text-slate-500">Check: </span>
              {c.label} — {c.reason}
              <Cite c={c.citation} />
            </li>
          ))}
        </ul>
      )}
      {it.citations.length > 0 && (
        <div className="mt-2 border-t border-slate-100 pt-1">
          {it.citations.map((c, i) => (
            <Cite key={i} c={c} />
          ))}
        </div>
      )}
    </li>
  );
}

function Cite({ c }: { c: DigestCitation }) {
  const href = pageLink(c.url, c.page);
  return (
    <div className="mt-1 text-xs text-slate-600">
      {href ? (
        <a className="text-blue-700 underline" href={href} target="_blank" rel="noreferrer">
          page {c.page}
        </a>
      ) : (
        <span>page {c.page}</span>
      )}{" "}
      · {c.document}
      <span className="block italic text-slate-700">“{c.quote}”</span>
    </div>
  );
}
