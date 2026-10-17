"use client";
/**
 * Master profile: edit every section (one PATCH /profile per save, each save an immutable new version),
 * browse earlier versions with what changed in each, and see the recorded facts with their evidence.
 */
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { FactsPanel } from "@/components/profile/FactsPanel";
import { ProfileEditor } from "@/components/profile/ProfileEditor";
import { VersionHistory } from "@/components/profile/VersionHistory";
import { ErrorNote, Shell, errText, useSession } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import type { VersionSummary } from "@/lib/types";
import { ELIGIBILITY_CHECK, type Consents } from "@/lib/types-privacy";
import type { ProfileFull } from "@/lib/types-profile-editor";

export default function ProfilePage() {
  const user = useSession();
  return <Shell user={user}>{user && <ProfileWorkspace />}</Shell>;
}

const TABS = [
  ["edit", "Edit"],
  ["history", "Version history"],
  ["facts", "Facts & evidence"],
] as const;
type Tab = (typeof TABS)[number][0];

function ProfileWorkspace() {
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [current, setCurrent] = useState<ProfileFull | null>(null);
  const [versions, setVersions] = useState<VersionSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<number | null>(null);
  const [tab, setTab] = useState<Tab>("edit");
  const [historyId, setHistoryId] = useState<string | null>(null);
  const [consented, setConsented] = useState<boolean | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [p, vs] = await Promise.all([
        api<ProfileFull>("/profile").catch((e) => {
          if (e instanceof ApiError && e.status === 404) return null;
          throw e;
        }),
        api<VersionSummary[]>("/profile/versions"),
      ]);
      const sorted = [...vs].sort((a, b) => b.version_number - a.version_number);
      setCurrent(p);
      setVersions(sorted);
      setHistoryId((id) => (id && sorted.some((v) => v.id === id) ? id : sorted[0]?.id ?? null));
      setState("ready");
    } catch (e) {
      setError(errText(e));
      setState((s) => (s === "ready" ? s : "error"));
    }
  }, []);

  useEffect(() => {
    load();
    api<Consents>("/privacy/consents")
      // A consent given under an older notice version no longer counts (the API answers CONSENT_OUTDATED)
      .then((c) => setConsented(c.active.includes(ELIGIBILITY_CHECK) && !c.outdated.includes(ELIGIBILITY_CHECK)))
      .catch(() => setConsented(null));
  }, [load]);

  async function onSaved(p: ProfileFull) {
    setSaved(p.version.version_number);
    setCurrent(p);
    setHistoryId(p.version.id);
    await load();
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-baseline gap-3">
        <h1 className="text-xl font-semibold">Profile</h1>
        {current && (
          <span className="text-sm text-slate-500">
            current version {current.version.version_number} of {versions.length}
          </span>
        )}
      </div>

      {consented === false && (
        <p className="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          Category, date of birth, disability, certificates and government service details can be saved only with your consent
          for eligibility checks under the current notice.{" "}
          <Link href="/privacy/consent" className="underline">
            Read the notice and give consent
          </Link>
        </p>
      )}

      {state === "loading" && <p className="text-slate-500">Loading profile…</p>}

      {state === "error" && (
        <div>
          <ErrorNote error={error} />
          <button
            type="button"
            onClick={() => {
              setState("loading");
              load();
            }}
            className="rounded border border-slate-300 px-3 py-1 text-sm hover:bg-slate-100"
          >
            Try again
          </button>
        </div>
      )}

      {state === "ready" && (
        <>
          <ErrorNote error={error} />
          {saved !== null && (
            <p className="rounded border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-800">
              Saved as version {saved}.{" "}
              <button type="button" className="underline" onClick={() => setTab("history")}>
                See what changed
              </button>
            </p>
          )}
          {!current && (
            <p className="rounded border border-slate-200 bg-white px-3 py-2 text-sm text-slate-600">
              No profile yet. Fill in any section below and save to create version 1.
            </p>
          )}

          <div role="tablist" className="flex gap-1 border-b border-slate-200">
            {TABS.map(([id, label]) => (
              <button
                key={id}
                role="tab"
                type="button"
                aria-selected={tab === id}
                onClick={() => setTab(id)}
                className={`-mb-px rounded-t border px-3 py-1.5 text-sm ${
                  tab === id ? "border-slate-200 border-b-white bg-white font-medium" : "border-transparent text-slate-600 hover:text-slate-900"
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          {/* Kept mounted while another tab is open so unsaved edits survive a look at the history */}
          <div hidden={tab !== "edit"}>
            <ProfileEditor key={current?.version.id ?? "new"} current={current} onSaved={onSaved} />
          </div>
          {tab === "history" && <VersionHistory versions={versions} currentId={current?.version.id ?? null} selectedId={historyId} onSelect={setHistoryId} />}
          {tab === "facts" && <FactsPanel versions={versions} versionId={current?.version.id ?? versions[0]?.id ?? null} />}
        </>
      )}
    </div>
  );
}
