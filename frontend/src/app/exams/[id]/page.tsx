"use client";
/**
 * One exam with its evidence trail: the dates and fees the notice states, the post-wise table (pay, age
 * limits and education when the notice gives them per post), the active requirements, and every stored
 * value with the quote and page it came from, each linked to the official document at that page.
 *
 * Eligibility is never computed implicitly: "Check my eligibility" calls POST /exams/{id}/eligibility for
 * the user's latest profile version (optionally one post) and shows every check with its reason. The newest
 * stored result for the same version and post is shown on load, labelled with when it was evaluated.
 */
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import { Badge, ErrorNote, Shell, errText, fmtDate, useSession } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import type { Profile, Provenance } from "@/lib/types";
import type { ExamDetail, ExamEligibility, ExamEligibilityIn, ExamPost } from "@/lib/types-exams";

export default function ExamPage() {
  const user = useSession();
  const { id } = useParams<{ id: string }>();
  return <Shell user={user}>{user && id && <ExamView id={id} />}</Shell>;
}

/** PDF viewers open at a page with the #page=N fragment. */
function pageLink(url: string | null, page: number | string | null): string | null {
  if (!url) return null;
  return page ? `${url}#page=${page}` : url;
}

/** A citation: "page N" linked to the official document at that page; plain text when no URL is stored. */
function Cite({ url, page, section }: { url: string | null; page: number | string | null; section?: string | null }) {
  const href = pageLink(url, page);
  const label = page ? `page ${page}` : "document";
  if (!href && !page) return <span className="text-slate-400">—</span>;
  return (
    <span className="whitespace-nowrap">
      {href ? (
        <a className="text-blue-700 underline" href={href} target="_blank" rel="noreferrer" title={url ?? undefined}>
          {label}
        </a>
      ) : (
        <span title="no document URL stored for this exam">{label}</span>
      )}
      {section && <span className="block text-xs text-slate-500">{section}</span>}
    </span>
  );
}

function Quote({ text }: { text: string | null }) {
  return text ? <span className="italic text-slate-700">“{text}”</span> : <span className="text-slate-400">—</span>;
}

function ageRange(p: ExamPost): string | null {
  if (p.age_min !== null && p.age_max !== null) return `${p.age_min}–${p.age_max} years`;
  if (p.age_max !== null) return `up to ${p.age_max} years`;
  if (p.age_min !== null) return `at least ${p.age_min} years`;
  return null;
}

function ExamView({ id }: { id: string }) {
  const [exam, setExam] = useState<ExamDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    let live = true;
    setExam(null);
    setError(null);
    setNotFound(false);
    api<ExamDetail>(`/exams/${id}`)
      .then((e) => live && setExam(e))
      .catch((e) => {
        if (!live) return;
        if (e instanceof ApiError && (e.status === 404 || e.status === 422)) setNotFound(true);
        setError(errText(e));
      });
    return () => {
      live = false;
    };
  }, [id]);

  /**
   * Official document for a raw document id: the URL stored with its provenance, else the notification URL,
   * but only for the exam's own notice (a corrigendum must never be cited as a page of the original notice).
   */
  const docUrl = useMemo(() => {
    const byDoc = new Map<string, string>();
    for (const p of exam?.provenance ?? []) if (p.document_url) byDoc.set(p.raw_document_id, p.document_url);
    return (rawDocumentId: string | null | undefined): string | null => {
      const stored = rawDocumentId ? byDoc.get(rawDocumentId) : undefined;
      if (stored) return stored;
      const ownNotice = !rawDocumentId || rawDocumentId === exam?.raw_document_id;
      return ownNotice ? exam?.notification_url ?? null : null;
    };
  }, [exam]);

  if (error)
    return (
      <div>
        <ErrorNote error={error} />
        {notFound && (
          <Link className="text-sm text-blue-700 underline" href="/exams">
            Back to exams
          </Link>
        )}
      </div>
    );
  if (!exam) return <p className="text-slate-500">Loading…</p>;

  const pdf = docUrl(exam.raw_document_id);
  const facts: [string, ReactNode][] = [
    ["Conducting body", exam.conducting_body],
    ["Exam code", exam.exam_code],
    ["Year", exam.exam_year],
    ["Applications open", exam.application_start ? fmtDate(exam.application_start, true) : null],
    ["Last date to apply", exam.application_end ? fmtDate(exam.application_end, true) : null],
    ["Last date for fee", exam.fee_payment_end ? fmtDate(exam.fee_payment_end, true) : null],
    [
      "Correction window",
      exam.correction_start || exam.correction_end ? `${fmtDate(exam.correction_start)} – ${fmtDate(exam.correction_end)}` : null,
    ],
    ["Fee (general)", exam.fee_amount !== null ? `₹${exam.fee_amount}` : null],
    ["Fee exemption", exam.fee_exemption],
    ["Age reckoned on", exam.age_crucial_date ? fmtDate(exam.age_crucial_date) : null],
    ["Education cut-off date", exam.education_cutoff_date ? fmtDate(exam.education_cutoff_date) : null],
    ["Tentative vacancies", exam.tentative_vacancies],
    ["Education", exam.education_text],
  ];

  return (
    <div className="space-y-6">
      <div>
        <Link className="text-sm text-slate-500 hover:text-slate-800" href="/exams">
          ← Exams
        </Link>
        <h1 className="mt-1 flex flex-wrap items-center gap-3 text-xl font-semibold">
          {exam.name} <Badge value={exam.status} />
        </h1>
        <p className="text-sm text-slate-500">
          {exam.conducting_body} · Ref {exam.external_ref} · parser confidence {(exam.confidence * 100).toFixed(0)}%
        </p>
        {exam.notification_url ? (
          <a className="mt-2 inline-block text-sm text-blue-700 underline" href={exam.notification_url} target="_blank" rel="noreferrer">
            Official notification
          </a>
        ) : (
          <p className="mt-2 text-sm text-amber-700">No official notification URL is stored for this exam.</p>
        )}
      </div>

      <section className="rounded border border-slate-200 bg-white p-4">
        <dl className="grid grid-cols-[11rem_1fr] gap-x-3 gap-y-1 text-sm">
          {facts
            .filter(([, val]) => val !== null && val !== undefined && val !== "")
            .map(([k, val]) => (
              <div key={k} className="contents">
                <dt className="text-slate-500">{k}</dt>
                <dd className="whitespace-pre-wrap">{val}</dd>
              </div>
            ))}
        </dl>
        <ExamDates dates={exam.exam_dates} />
      </section>

      <section>
        <h2 className="mb-2 font-medium">Posts</h2>
        <PostsTable posts={exam.posts} pdf={pdf} />
      </section>

      <section>
        <h2 className="mb-2 font-medium">Requirements</h2>
        <ul className="space-y-2">
          {exam.requirements.map((r) => (
            <li key={r.id} className="rounded border border-slate-200 bg-white p-3 text-sm">
              <div className="mb-1 flex flex-wrap items-center gap-2">
                <Badge value={r.req_type} />
                {!r.is_mandatory && <span className="text-xs text-slate-500">desirable</span>}
                <span className="ml-auto text-xs">
                  <Cite url={pdf} page={r.source_page} section={r.source_section} />
                </span>
              </div>
              <p className="whitespace-pre-wrap">{r.value}</p>
            </li>
          ))}
          {!exam.requirements.length && <li className="text-sm text-slate-500">No requirements extracted.</li>}
        </ul>
      </section>

      <section>
        <h2 className="mb-2 font-medium">Where each value comes from</h2>
        <ProvenanceTable rows={exam.provenance} docUrl={docUrl} />
      </section>

      <section>
        <h2 className="mb-2 font-medium">Your eligibility</h2>
        <EligibilitySection exam={exam} pdf={pdf} />
      </section>
    </div>
  );
}

function ExamDates({ dates }: { dates: ExamDetail["exam_dates"] }) {
  if (!dates) return null;
  const rows: [string, string][] = Array.isArray(dates)
    ? dates.map((d, i) => [String(d.stage ?? `Stage ${i + 1}`), String(d.schedule ?? JSON.stringify(d))])
    : Object.entries(dates).map(([k, v]) => [k, typeof v === "string" ? v : JSON.stringify(v)]);
  if (!rows.length) return null;
  return (
    <div className="mt-3 border-t border-slate-100 pt-3">
      <h3 className="mb-1 text-sm font-medium">Exam schedule</h3>
      <dl className="grid grid-cols-[11rem_1fr] gap-x-3 gap-y-1 text-sm">
        {rows.map(([stage, schedule], i) => (
          <div key={`${stage}-${i}`} className="contents">
            <dt className="text-slate-500">{stage}</dt>
            <dd>{schedule}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function PostsTable({ posts, pdf }: { posts: ExamPost[]; pdf: string | null }) {
  if (!posts.length) return <p className="text-sm text-slate-500">The notice text used does not list posts for this exam.</p>;
  return (
    <div className="overflow-x-auto rounded border border-slate-200 bg-white">
      <table className="w-full text-sm">
        <thead className="bg-slate-100 text-left">
          <tr>
            <th className="px-3 py-2">Post</th>
            <th className="px-3 py-2">Pay</th>
            <th className="px-3 py-2">Age limit</th>
            <th className="px-3 py-2">Education</th>
            <th className="px-3 py-2 text-right">Vacancies</th>
            <th className="px-3 py-2">Source</th>
          </tr>
        </thead>
        <tbody>
          {posts.map((p) => (
            <tr key={p.id} className="border-t border-slate-100 align-top">
              <td className="px-3 py-2">
                <div className="font-medium">{p.post_name}</div>
                <div className="text-xs text-slate-500">
                  {[p.post_code && `code ${p.post_code}`, p.department].filter(Boolean).join(" · ") || null}
                </div>
              </td>
              <td className="px-3 py-2">
                {p.pay_level || p.pay_scale ? (
                  <>
                    {p.pay_level && <div>{p.pay_level}</div>}
                    {p.pay_scale && <div className="text-xs text-slate-600">{p.pay_scale}</div>}
                  </>
                ) : (
                  "—"
                )}
              </td>
              <td className="whitespace-nowrap px-3 py-2">{ageRange(p) ?? "—"}</td>
              <td className="px-3 py-2 whitespace-pre-wrap">{p.education_text ?? "—"}</td>
              <td className="px-3 py-2 text-right">{p.vacancy_count ?? "—"}</td>
              <td className="px-3 py-2">
                <Cite url={pdf} page={p.source_page} />
                {p.source_excerpt && (
                  <div className="mt-1 max-w-xs text-xs">
                    <Quote text={p.source_excerpt} />
                  </div>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ProvenanceTable({ rows, docUrl }: { rows: Provenance[]; docUrl: (rawDocumentId: string) => string | null }) {
  if (!rows.length) return <p className="text-sm text-slate-500">No field provenance recorded.</p>;
  return (
    <div className="overflow-x-auto rounded border border-slate-200 bg-white">
      <table className="w-full text-sm">
        <thead className="bg-slate-100 text-left">
          <tr>
            <th className="px-3 py-2">Field</th>
            <th className="px-3 py-2">Value</th>
            <th className="px-3 py-2">Quote from the document</th>
            <th className="px-3 py-2">Source</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => (
            <tr key={`${p.field_name}-${p.raw_document_id}`} className="border-t border-slate-100 align-top">
              <td className="px-3 py-2 font-mono text-xs">{p.field_name}</td>
              <td className="px-3 py-2 whitespace-pre-wrap">{p.value ?? "—"}</td>
              <td className="px-3 py-2">
                <Quote text={p.excerpt} />
              </td>
              <td className="px-3 py-2">
                <Cite url={p.document_url ?? docUrl(p.raw_document_id)} page={p.page} section={p.section} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EligibilitySection({ exam, pdf }: { exam: ExamDetail; pdf: string | null }) {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [profileNote, setProfileNote] = useState<ReactNode>(null);
  const [history, setHistory] = useState<ExamEligibility[]>([]);
  const [postId, setPostId] = useState<string>("");
  const [result, setResult] = useState<ExamEligibility | null>(null);
  const [freshId, setFreshId] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    (async () => {
      let p: Profile;
      try {
        p = await api<Profile>("/profile");
      } catch (e) {
        if (!live) return;
        setProfileNote(
          e instanceof ApiError && e.status === 404 ? (
            <>
              No profile yet. <Link className="text-blue-700 underline" href="/profile">Create one</Link> to check eligibility.
            </>
          ) : (
            errText(e)
          ),
        );
        return;
      }
      if (!live) return;
      setProfile(p);
      try {
        const rows = await api<ExamEligibility[]>(`/exams/${exam.id}/eligibility?limit=200`);
        if (live) setHistory(rows);
      } catch (e) {
        if (live) setError(`Could not load earlier results: ${errText(e)}`);
      }
    })();
    return () => {
      live = false;
    };
  }, [exam.id]);

  // Show the newest stored result for the current profile version and the selected post (if any).
  useEffect(() => {
    if (!profile) return;
    const stored = history.find((r) => r.profile_version_id === profile.version.id && (r.post_id ?? "") === postId);
    setResult(stored ?? null);
  }, [history, profile, postId]);

  async function check() {
    if (!profile) return;
    setRunning(true);
    setError(null);
    try {
      const body: ExamEligibilityIn = { profile_version_id: profile.version.id, post_id: postId || null };
      const r = await api<ExamEligibility>(`/exams/${exam.id}/eligibility`, { method: "POST", body });
      setFreshId(r.id);
      setHistory((h) => [r, ...h]);
    } catch (e) {
      setError(errText(e));
    } finally {
      setRunning(false);
    }
  }

  if (profileNote) return <p className="text-sm text-slate-600">{profileNote}</p>;
  if (!profile) return <p className="text-sm text-slate-500">Loading your profile…</p>;

  const postName = (pid: string | null) => exam.posts.find((p) => p.id === pid)?.post_name;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span className="text-slate-500">Profile version {profile.version.version_number}</span>
        {exam.posts.length > 0 && (
          <label className="flex items-center gap-2">
            <span className="text-slate-500">Post</span>
            <select value={postId} onChange={(e) => setPostId(e.target.value)} className="rounded border border-slate-300 px-2 py-1">
              <option value="">Whole exam (common conditions)</option>
              {exam.posts.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.post_name}
                  {p.post_code ? ` (${p.post_code})` : ""}
                </option>
              ))}
            </select>
          </label>
        )}
        <button
          onClick={check}
          disabled={running}
          className="rounded bg-blue-700 px-3 py-1.5 font-medium text-white hover:bg-blue-800 disabled:opacity-50"
        >
          {running ? "Checking…" : "Check my eligibility"}
        </button>
      </div>
      <ErrorNote error={error} />
      {result ? (
        <EligibilityPanel e={result} pdf={pdf} fresh={result.id === freshId} postName={postName(result.post_id)} />
      ) : (
        !running && (
          <p className="text-sm text-slate-500">
            Not checked yet for profile version {profile.version.version_number}
            {postId ? ` and ${postName(postId) ?? "this post"}` : ""}.
          </p>
        )
      )}
    </div>
  );
}

function EligibilityPanel({ e, pdf, fresh, postName }: { e: ExamEligibility; pdf: string | null; fresh: boolean; postName?: string }) {
  return (
    <div className="rounded border border-slate-200 bg-white p-4">
      <div className="mb-3 flex flex-wrap items-center gap-3 text-sm">
        <Badge value={e.status} />
        <span className="text-slate-500">
          {postName ? `${postName} · ` : "whole exam · "}
          engine {e.engine_version} · {fresh ? "evaluated just now" : `stored result from ${fmtDate(e.created_at, true)}`}
          {e.crucial_date && ` · crucial date ${fmtDate(e.crucial_date)}`}
        </span>
      </div>
      {!e.evidence.length && <p className="text-sm text-slate-500">The engine recorded no checks for this result.</p>}
      <ul className="space-y-3">
        {e.evidence.map((c, i) => (
          <li key={`${c.check_name}-${i}`} className="border-t border-slate-100 pt-2 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{c.check_name}</span>
              <Badge value={c.outcome} />
              {c.match_type && <span className="font-mono text-xs text-slate-500">{c.match_type}</span>}
              {c.source_page && (
                <span className="ml-auto text-xs">
                  <Cite url={pdf} page={c.source_page} />
                </span>
              )}
            </div>
            <p>
              <span className="text-slate-500">Reason: </span>
              {c.rule}
            </p>
            <p>
              <span className="text-slate-500">Notification says: </span>
              {c.requirement}
            </p>
            <p>
              <span className="text-slate-500">Your profile: </span>
              {c.profile_evidence}
            </p>
          </li>
        ))}
      </ul>
    </div>
  );
}
