"use client";
/**
 * Matches for the latest profile version. The backend never implies "the current profile", so the page
 * resolves the latest version id first and passes it explicitly; the version number is shown so the
 * user knows which answers the statuses were computed from.
 *
 * Each row opens the stored relevance breakdown (six components with score, weight and evidence, the
 * unscored ones with the reason, and the official passages each one reads). The list can be filtered by
 * eligibility status and deadline window, and the radar can be run on demand.
 */
import Link from "next/link";
import { Fragment, useCallback, useEffect, useState } from "react";

import { MatchFilters, inDeadlineWindow, matchesStatus } from "@/components/matches/MatchFilters";
import { RadarRunButton } from "@/components/matches/RadarRunButton";
import { RelevanceBreakdown } from "@/components/matches/RelevanceBreakdown";
import { Badge, ErrorNote, Shell, errText, fmtDate, useSession } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import type { Match, Profile } from "@/lib/types";
import type { DeadlineWindow, EligibilityFilter } from "@/lib/types-matches-relevance";

export default function MatchesPage() {
  const user = useSession();
  return <Shell user={user}>{user && <MatchesView />}</Shell>;
}

function MatchesView() {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [matches, setMatches] = useState<Match[] | null>(null);
  const [includeClosed, setIncludeClosed] = useState(false);
  const [noProfile, setNoProfile] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [loadedAt, setLoadedAt] = useState(() => Date.now());
  const [status, setStatus] = useState<EligibilityFilter>("ALL");
  const [deadline, setDeadline] = useState<DeadlineWindow>("any");
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    (async () => {
      setError(null);
      setMatches(null);
      setOpen(null);
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
      try {
        const ms = await api<Match[]>(`/matches?profile_version_id=${p.version.id}&include_closed=${includeClosed}`);
        if (live) {
          setMatches(ms);
          setLoadedAt(Date.now());
        }
      } catch (e) {
        if (live) setError(errText(e));
      }
    })();
    return () => {
      live = false;
    };
  }, [includeClosed, reloadKey]);

  const reload = useCallback(() => setReloadKey((k) => k + 1), []);

  function toggleClosed(checked: boolean) {
    setIncludeClosed(checked);
    if (!checked && deadline === "closed") setDeadline("any");
  }

  if (noProfile)
    return (
      <p>
        No profile yet. <Link className="text-blue-700 underline" href="/profile">Create one</Link> to see matches.
      </p>
    );

  const shown = matches?.filter((m) => matchesStatus(m, status) && inDeadlineWindow(m, deadline, loadedAt)) ?? null;

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-start gap-4">
        <div>
          <h1 className="text-xl font-semibold">Matches</h1>
          {profile && <span className="text-sm text-slate-500">for profile version {profile.version.version_number}</span>}
        </div>
        <div className="ml-auto flex flex-col items-end gap-2">
          <RadarRunButton onDone={reload} />
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={includeClosed} onChange={(e) => toggleClosed(e.target.checked)} />
            Include closed vacancies
          </label>
        </div>
      </div>
      <ErrorNote error={error} />
      {matches === null && !error && <p className="text-slate-500">Evaluating…</p>}
      {matches?.length === 0 && (
        <p className="text-slate-500">
          {includeClosed ? "No active vacancies in the database yet." : "No open vacancies in the database right now."}
        </p>
      )}
      {matches && matches.length > 0 && (
        <>
          <MatchFilters
            matches={matches}
            status={status}
            onStatus={setStatus}
            deadline={deadline}
            onDeadline={setDeadline}
            includeClosed={includeClosed}
            now={loadedAt}
          />
          {shown && shown.length === 0 ? (
            <p className="rounded border border-slate-200 bg-white px-3 py-4 text-sm text-slate-500">
              No matches for this filter ({matches.length} in total).{" "}
              <button
                className="text-blue-700 underline"
                onClick={() => {
                  setStatus("ALL");
                  setDeadline("any");
                }}
              >
                Clear filters
              </button>
            </p>
          ) : (
            <div className="overflow-x-auto rounded border border-slate-200 bg-white">
              <table className="w-full text-sm">
                <thead className="bg-slate-100 text-left">
                  <tr>
                    <th className="px-3 py-2">Post</th>
                    <th className="px-3 py-2">Eligibility</th>
                    <th className="px-3 py-2 text-right">Relevance</th>
                    <th className="px-3 py-2">Last date</th>
                  </tr>
                </thead>
                <tbody>
                  {shown?.map((m) => {
                    const isOpen = open === m.vacancy_id;
                    return (
                      <Fragment key={m.vacancy_id}>
                        <tr className="border-t border-slate-100 align-top">
                          <td className="px-3 py-2">
                            <Link href={`/vacancies/${m.vacancy_id}`} className="font-medium text-blue-700 hover:underline">
                              {m.post_name}
                            </Link>
                            <div className="text-slate-500">
                              {m.organization}
                              {m.department ? ` · ${m.department}` : ""}
                            </div>
                          </td>
                          <td className="px-3 py-2">
                            <Badge value={m.eligibility_status} />
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            <RelevanceBar score={m.relevance_score} />
                            <button
                              className="mt-1 text-xs text-blue-700 underline"
                              aria-expanded={isOpen}
                              onClick={() => setOpen(isOpen ? null : m.vacancy_id)}
                            >
                              {isOpen ? "Hide breakdown" : "Why this score"}
                            </button>
                          </td>
                          <td className="px-3 py-2">
                            {fmtDate(m.application_deadline)}
                            {!m.open_for_applications && <span className="ml-1 text-xs text-red-700">closed</span>}
                          </td>
                        </tr>
                        {isOpen && (
                          <tr className="bg-slate-50/60">
                            <td colSpan={4} className="px-3 py-3">
                              <RelevanceBreakdown relevanceId={m.relevance_id} vacancyId={m.vacancy_id} />
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          {shown && shown.length > 0 && shown.length < matches.length && (
            <p className="mt-2 text-xs text-slate-500">
              Showing {shown.length} of {matches.length} matches.
            </p>
          )}
        </>
      )}
      <p className="mt-3 text-xs text-slate-500">
        Eligibility is decided by fixed rules from the official notification; relevance (0–100) only ranks and never changes eligibility.
      </p>
    </div>
  );
}

function RelevanceBar({ score }: { score: number }) {
  const pct = Math.max(0, Math.min(100, score));
  return (
    <div className="flex items-center justify-end gap-2">
      <div className="h-1.5 w-20 rounded bg-slate-200">
        <div className="h-1.5 rounded bg-blue-600" style={{ width: `${pct}%` }} />
      </div>
      <span>{score.toFixed(0)}</span>
    </div>
  );
}
