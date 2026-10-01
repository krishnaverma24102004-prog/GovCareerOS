"use client";
/**
 * Gaps for the latest profile version: the most common blockers across active vacancies
 * (GET /gaps/summary), then, per vacancy, every gap with the notification's own words and page,
 * what the profile has, the deterministic next step and its due date (GET /gaps).
 *
 * The backend never implies "the current profile", so the latest version id is resolved first and
 * passed explicitly. The vacancy list comes from GET /matches for the same version and filter; a
 * vacancy's gaps (and its official document URL, from GET /vacancies/{id}) load when it is opened.
 */
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge, ErrorNote, Shell, errText, fmtDate, useSession } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import type { Match, Profile, VacancyDetail } from "@/lib/types";
import type { Blocker, Gap, Gaps, GapsSummary } from "@/lib/types-gaps";

export default function GapsPage() {
  const user = useSession();
  return <Shell user={user}>{user && <GapsView />}</Shell>;
}

/** YYYY-MM-DD as a calendar date, without a timezone shift. */
function fmtDay(iso: string | null | undefined): string {
  if (!iso) return "—";
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return fmtDate(iso);
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

/** PDF viewers open at a page with the #page=N fragment. */
function pageLink(url: string, page: number | null): string {
  return page ? `${url}#page=${page}` : url;
}

/** The official URL of the vacancy's own source document, else its notification URL. */
function documentUrl(v: VacancyDetail): string | null {
  const own = v.raw_document_id ? v.provenance.find((p) => p.raw_document_id === v.raw_document_id && p.document_url) : undefined;
  return own?.document_url ?? v.provenance.find((p) => p.document_url)?.document_url ?? v.notification_url;
}

function label(s: string): string {
  return s.replace(/_/g, " ");
}

function GapsView() {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [noProfile, setNoProfile] = useState(false);
  const [includeClosed, setIncludeClosed] = useState(false);
  const [summary, setSummary] = useState<GapsSummary | null>(null);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [vacancies, setVacancies] = useState<Match[] | null>(null);
  const [vacanciesError, setVacanciesError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<{ title: string; ids: Set<string> } | null>(null);
  const [open, setOpen] = useState<Set<string>>(new Set());

  useEffect(() => {
    let live = true;
    (async () => {
      setError(null);
      setSummary(null);
      setSummaryError(null);
      setVacancies(null);
      setVacanciesError(null);
      setFilter(null);
      setOpen(new Set());
      let p: Profile;
      try {
        p = await api<Profile>("/profile");
      } catch (e) {
        if (!live) return;
        if (e instanceof ApiError && e.status === 404) setNoProfile(true);
        else setError(errText(e));
        return;
      }
      if (!live) return;
      setProfile(p);
      const q = `profile_version_id=${encodeURIComponent(p.version.id)}&include_closed=${includeClosed}`;
      // One after the other: both walk every active vacancy, and the second reuses what the first stored.
      try {
        const s = await api<GapsSummary>(`/gaps/summary?${q}`);
        if (live) setSummary(s);
      } catch (e) {
        if (live) setSummaryError(errText(e));
      }
      if (!live) return;
      try {
        const ms = await api<Match[]>(`/matches?${q}`);
        if (live) setVacancies(ms);
      } catch (e) {
        if (live) setVacanciesError(errText(e));
      }
    })();
    return () => {
      live = false;
    };
  }, [includeClosed]);

  const toggle = useCallback((id: string) => {
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  function showBlocker(b: Blocker) {
    const ids = new Set(b.vacancy_ids);
    setFilter({ title: `${label(b.gap_type)}${b.reason_code ? ` · ${label(b.reason_code)}` : ""}`, ids });
    // Open them only when there are few enough that loading each one stays quick.
    setOpen(ids.size <= 5 ? new Set(ids) : new Set());
    document.getElementById("per-vacancy")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  const shown = useMemo(
    () => (vacancies && filter ? vacancies.filter((m) => filter.ids.has(m.vacancy_id)) : vacancies),
    [vacancies, filter],
  );

  if (noProfile)
    return (
      <p>
        No profile yet. <Link className="text-blue-700 underline" href="/profile">Create one</Link> to see what stands between
        you and each vacancy.
      </p>
    );
  if (error) return <ErrorNote error={error} />;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-4">
        <h1 className="text-xl font-semibold">Gaps</h1>
        {profile && <span className="text-sm text-slate-500">for profile version {profile.version.version_number}</span>}
        <label className="ml-auto flex items-center gap-2 text-sm">
          <input type="checkbox" checked={includeClosed} onChange={(e) => setIncludeClosed(e.target.checked)} />
          Include closed vacancies
        </label>
      </div>

      <section>
        <h2 className="mb-2 font-medium">Most common blockers</h2>
        <ErrorNote error={summaryError} />
        {!summary && !summaryError && <p className="text-sm text-slate-500">Working out gaps across every vacancy…</p>}
        {summary && <SummaryPanel s={summary} onShow={showBlocker} />}
      </section>

      <section id="per-vacancy" className="scroll-mt-4">
        <div className="mb-2 flex flex-wrap items-center gap-3">
          <h2 className="font-medium">Per vacancy</h2>
          {filter && (
            <span className="flex items-center gap-2 rounded bg-blue-50 px-2 py-0.5 text-sm text-blue-800">
              blocked by {filter.title}
              <button className="text-blue-700 underline" onClick={() => setFilter(null)}>
                show all
              </button>
            </span>
          )}
        </div>
        <ErrorNote error={vacanciesError} />
        {vacancies === null && !vacanciesError && <p className="text-sm text-slate-500">Loading vacancies…</p>}
        {shown?.length === 0 && (
          <p className="text-sm text-slate-500">
            {filter ? "None of the listed vacancies is in the current list." : includeClosed
              ? "No active vacancies in the database yet."
              : "No vacancy is open for applications; tick \"Include closed vacancies\" to see the rest."}
          </p>
        )}
        {shown && shown.length > 0 && (
          <ul className="space-y-2">
            {shown.map((m) => (
              <VacancyRow
                key={m.vacancy_id}
                m={m}
                profileVersionId={profile!.version.id}
                open={open.has(m.vacancy_id)}
                onToggle={() => toggle(m.vacancy_id)}
              />
            ))}
          </ul>
        )}
      </section>

      <p className="text-xs text-slate-500">
        Gaps come from fixed rules over the official notification and your profile; a blocking gap stands between you and
        eligibility, the others are conditions to prepare for or ways to rank higher. Always confirm against the notification.
      </p>
    </div>
  );
}

function SummaryPanel({ s, onShow }: { s: GapsSummary; onShow: (b: Blocker) => void }) {
  if (s.vacancies_evaluated === 0) return <p className="text-sm text-slate-500">No active vacancies to evaluate yet.</p>;
  return (
    <div className="space-y-3">
      <p className="text-sm text-slate-600">
        {s.vacancies_without_blockers} of {s.vacancies_evaluated} vacanc{s.vacancies_evaluated === 1 ? "y has" : "ies have"} no
        blocking gap.
      </p>
      {s.blockers.length === 0 ? (
        <p className="text-sm text-slate-500">Nothing blocks you anywhere.</p>
      ) : (
        <ul className="space-y-2">
          {s.blockers.map((b) => (
            <li
              key={`${b.gap_type}|${b.reason_code ?? ""}|${b.profile_field ?? ""}`}
              className="rounded border border-slate-200 bg-white p-3 text-sm"
            >
              <div className="flex flex-wrap items-center gap-2">
                <Badge value={b.gap_type} />
                {b.reason_code && <span className="font-mono text-xs text-slate-500">{b.reason_code}</span>}
                {b.profile_field && <span className="text-xs text-slate-500">profile field: {label(b.profile_field)}</span>}
                <span className="ml-auto font-medium">
                  {b.vacancies} vacanc{b.vacancies === 1 ? "y" : "ies"}
                </span>
              </div>
              {b.example_next_step && (
                <p className="mt-1">
                  <span className="text-slate-500">Next step: </span>
                  {b.example_next_step}
                </p>
              )}
              {b.earliest_due_date && (
                <p>
                  <span className="text-slate-500">Earliest due: </span>
                  {fmtDay(b.earliest_due_date)}
                </p>
              )}
              <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-1">
                {b.vacancy_ids.map((id, i) => (
                  <Link key={id} href={`/vacancies/${id}`} className="text-blue-700 hover:underline">
                    {b.posts[i] ?? id}
                  </Link>
                ))}
                <button className="ml-auto text-xs text-blue-700 underline" onClick={() => onShow(b)}>
                  show these gaps
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function VacancyRow({ m, profileVersionId, open, onToggle }: { m: Match; profileVersionId: string; open: boolean; onToggle: () => void }) {
  return (
    <li className="rounded border border-slate-200 bg-white">
      <div className="flex flex-wrap items-center gap-3 px-3 py-2 text-sm">
        <button onClick={onToggle} aria-expanded={open} className="w-4 text-slate-500" title={open ? "Hide gaps" : "Show gaps"}>
          {open ? "▾" : "▸"}
        </button>
        <div className="min-w-0 flex-1">
          <Link href={`/vacancies/${m.vacancy_id}`} className="font-medium text-blue-700 hover:underline">
            {m.post_name}
          </Link>
          <div className="text-slate-500">
            {m.organization}
            {m.department ? ` · ${m.department}` : ""} · last date {fmtDate(m.application_deadline)}
            {!m.open_for_applications && <span className="ml-1 text-xs text-red-700">closed</span>}
          </div>
        </div>
        <Badge value={m.eligibility_status} />
        <button onClick={onToggle} className="text-xs text-blue-700 underline">
          {open ? "hide gaps" : "show gaps"}
        </button>
      </div>
      {open && <VacancyGaps vacancyId={m.vacancy_id} profileVersionId={profileVersionId} />}
    </li>
  );
}

function VacancyGaps({ vacancyId, profileVersionId }: { vacancyId: string; profileVersionId: string }) {
  const [data, setData] = useState<Gaps | null>(null);
  const [vacancy, setVacancy] = useState<VacancyDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [docError, setDocError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    const q = `profile_version_id=${encodeURIComponent(profileVersionId)}&vacancy_id=${encodeURIComponent(vacancyId)}`;
    api<Gaps>(`/gaps?${q}`)
      .then((g) => live && setData(g))
      .catch((e) => live && setError(errText(e)));
    api<VacancyDetail>(`/vacancies/${encodeURIComponent(vacancyId)}`)
      .then((v) => live && setVacancy(v))
      .catch((e) => live && setDocError(`official document link unavailable: ${errText(e)}`));
    return () => {
      live = false;
    };
  }, [vacancyId, profileVersionId]);

  if (error) return <div className="border-t border-slate-100 px-3 pb-2"><ErrorNote error={error} /></div>;
  if (!data) return <p className="border-t border-slate-100 px-3 py-2 text-sm text-slate-500">Loading gaps…</p>;

  const docUrl = vacancy ? documentUrl(vacancy) : null;
  const sections = new Map((vacancy?.requirements ?? []).map((r) => [r.id, r.source_section]));
  const blocking = data.gaps.filter((g) => g.blocking).length;

  return (
    <div className="border-t border-slate-100 px-3 py-2">
      <p className="mb-2 text-xs text-slate-500">
        eligibility <Badge value={data.eligibility_status} /> · {blocking} blocking of {data.gaps.length} gap
        {data.gaps.length === 1 ? "" : "s"} · engine {data.engine_version}
      </p>
      {docError && <p className="mb-2 text-xs text-amber-800">{docError}</p>}
      {data.gaps.length === 0 ? (
        <p className="text-sm text-slate-500">No gaps: nothing stands between this profile version and this vacancy.</p>
      ) : (
        <ul className="space-y-3">
          {data.gaps.map((g) => (
            <GapItem key={g.id} g={g} docUrl={docUrl} docLoading={!vacancy && !docError} section={g.requirement_id ? sections.get(g.requirement_id) ?? null : null} />
          ))}
        </ul>
      )}
    </div>
  );
}

function GapItem({ g, docUrl, docLoading, section }: { g: Gap; docUrl: string | null; docLoading: boolean; section: string | null }) {
  return (
    <li className={`border-l-4 pl-3 text-sm ${g.blocking ? "border-red-400" : "border-amber-300"}`}>
      <div className="flex flex-wrap items-center gap-2">
        <Badge value={g.gap_type} />
        <span className={`rounded px-2 py-0.5 text-xs font-medium ${g.blocking ? "bg-red-100 text-red-800" : "bg-slate-100 text-slate-700"}`}>
          {g.blocking ? "blocking" : "not blocking"}
        </span>
        {g.reason_code && <span className="font-mono text-xs text-slate-500">{g.reason_code}</span>}
        <span className="ml-auto text-xs">
          <Citation page={g.requirement_page} docUrl={docUrl} docLoading={docLoading} section={section} />
        </span>
      </div>
      <p className="mt-1">
        <span className="text-slate-500">Requirement: </span>
        {g.requirement}
      </p>
      {g.requirement_quote && (
        <blockquote className="my-1 whitespace-pre-wrap border-l-2 border-slate-200 pl-2 italic text-slate-700">
          “{g.requirement_quote}”
        </blockquote>
      )}
      <p>
        <span className="text-slate-500">Your profile: </span>
        {g.profile_has || "—"}
        {g.profile_field && <span className="ml-1 font-mono text-xs text-slate-500">({g.profile_field})</span>}
      </p>
      <p>
        <span className="text-slate-500">Next step: </span>
        {g.next_step || "—"}
      </p>
      {g.due_date && (
        <p>
          <span className="text-slate-500">Due: </span>
          <span className="font-medium">{fmtDay(g.due_date)}</span>
        </p>
      )}
    </li>
  );
}

function Citation({ page, docUrl, docLoading, section }: { page: number | null; docUrl: string | null; docLoading: boolean; section: string | null }) {
  const text = `${page !== null ? `page ${page}` : "official document"}${section ? ` · ${section}` : ""}`;
  if (docUrl)
    return (
      <a className="text-blue-700 underline" href={pageLink(docUrl, page)} target="_blank" rel="noreferrer">
        {text}
      </a>
    );
  if (page === null) return null;
  return <span className="text-slate-500">{docLoading ? text : `${text} (no document link)`}</span>;
}
