"use client";
/**
 * Matches for the latest profile version. The backend never implies "the current profile", so the page
 * resolves the latest version id first and passes it explicitly; the version number is shown so the
 * user knows which answers the statuses were computed from.
 */
import Link from "next/link";
import { useEffect, useState } from "react";

import { Badge, ErrorNote, Shell, errText, fmtDate, useSession } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import type { Match, Profile } from "@/lib/types";

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

  useEffect(() => {
    let live = true;
    (async () => {
      setError(null);
      setMatches(null);
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
        if (live) setMatches(ms);
      } catch (e) {
        if (live) setError(errText(e));
      }
    })();
    return () => {
      live = false;
    };
  }, [includeClosed]);

  if (noProfile)
    return (
      <p>
        No profile yet. <Link className="text-blue-700 underline" href="/profile">Create one</Link> to see matches.
      </p>
    );

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-4">
        <h1 className="text-xl font-semibold">Matches</h1>
        {profile && <span className="text-sm text-slate-500">for profile version {profile.version.version_number}</span>}
        <label className="ml-auto flex items-center gap-2 text-sm">
          <input type="checkbox" checked={includeClosed} onChange={(e) => setIncludeClosed(e.target.checked)} />
          Include closed vacancies
        </label>
      </div>
      <ErrorNote error={error} />
      {matches === null && !error && <p className="text-slate-500">Evaluating…</p>}
      {matches?.length === 0 && <p className="text-slate-500">No active vacancies in the database yet.</p>}
      {matches && matches.length > 0 && (
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
              {matches.map((m) => (
                <tr key={m.vacancy_id} className="border-t border-slate-100">
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
                  </td>
                  <td className="px-3 py-2">
                    {fmtDate(m.application_deadline)}
                    {!m.open_for_applications && <span className="ml-1 text-xs text-red-700">closed</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
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
