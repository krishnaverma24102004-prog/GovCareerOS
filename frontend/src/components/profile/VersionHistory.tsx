"use client";
/**
 * Every saved version with what changed against the version before it, plus a read-only snapshot.
 * Versions are fetched on demand (GET /profile/versions/{id}) and kept for the life of the page.
 */
import { useEffect, useRef, useState } from "react";

import { Badge, ErrorNote, errText, fmtDate } from "@/components/ui";
import { api } from "@/lib/api";
import type { VersionSummary } from "@/lib/types";
import type { ProfileFull } from "@/lib/types-profile-editor";

import { diffProfiles, type FieldChange, type ProfileDiff } from "./diff";
import { PERSONAL_FIELDS, PERSONAL_LABELS, SECTIONS, display, sectionRows } from "./sections";

export function VersionHistory({
  versions,
  currentId,
  selectedId,
  onSelect,
}: {
  versions: VersionSummary[];
  currentId: string | null;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const cache = useRef(new Map<string, ProfileFull>());
  const [shown, setShown] = useState<{ version: ProfileFull; previous: ProfileFull | null; diff: ProfileDiff } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!selectedId) return;
    let live = true;
    const fetchVersion = async (id: string) => {
      const hit = cache.current.get(id);
      if (hit) return hit;
      const p = await api<ProfileFull>(`/profile/versions/${id}`);
      cache.current.set(id, p);
      return p;
    };
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const sel = versions.find((v) => v.id === selectedId);
        const prev = sel
          ? versions.filter((v) => v.version_number < sel.version_number).sort((a, b) => b.version_number - a.version_number)[0]
          : undefined;
        const [version, previous] = await Promise.all([fetchVersion(selectedId), prev ? fetchVersion(prev.id) : Promise.resolve(null)]);
        if (live) setShown({ version, previous, diff: diffProfiles(previous, version) });
      } catch (e) {
        if (live) setError(errText(e));
      } finally {
        if (live) setLoading(false);
      }
    })();
    return () => {
      live = false;
    };
  }, [selectedId, versions]);

  if (!versions.length) return <p className="text-slate-600">No versions yet. Save the profile once to create version 1.</p>;

  return (
    <div className="grid gap-6 md:grid-cols-[15rem_1fr]">
      <aside>
        <h2 className="mb-2 font-medium">Versions ({versions.length})</h2>
        <ul className="space-y-1 text-sm">
          {versions.map((v) => (
            <li key={v.id}>
              <button
                type="button"
                onClick={() => onSelect(v.id)}
                className={`w-full rounded px-2 py-1 text-left hover:bg-slate-100 ${selectedId === v.id ? "bg-slate-200" : ""}`}
              >
                <span className="font-medium">v{v.version_number}</span> · {fmtDate(v.created_at, true)}
                {v.id === currentId && <span className="ml-1 text-xs text-blue-700">current</span>}
                {v.change_summary && <span className="block truncate text-slate-500">{v.change_summary}</span>}
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <section className="min-w-0 space-y-4">
        <ErrorNote error={error} />
        {loading && <p className="text-sm text-slate-500">Loading version…</p>}
        {shown && !loading && (
          <>
            <div className="rounded border border-slate-200 bg-white p-4">
              <h2 className="mb-1 font-medium">
                Version {shown.version.version.version_number}{" "}
                {shown.version.version.id === currentId ? <Badge value="CURRENT" /> : <Badge value="EARLIER VERSION" />}
              </h2>
              <p className="mb-3 text-sm text-slate-600">
                Saved {fmtDate(shown.version.version.created_at, true)}
                {shown.version.version.change_summary ? ` — “${shown.version.version.change_summary}”` : ""}
              </p>
              <h3 className="mb-2 text-sm font-medium">
                {shown.previous ? `What changed since version ${shown.previous.version.version_number}` : "First version: everything below was added"}
              </h3>
              <DiffView diff={shown.diff} />
            </div>
            <Snapshot profile={shown.version} />
          </>
        )}
      </section>
    </div>
  );
}

function val(v: unknown) {
  return <span className="break-words">{display(v)}</span>;
}

function FieldChanges({ changes }: { changes: FieldChange[] }) {
  return (
    <ul className="space-y-0.5">
      {changes.map((c) => (
        <li key={c.field}>
          <span className="text-slate-500">{c.label}:</span> <del className="text-red-700">{val(c.before)}</del> →{" "}
          <ins className="text-green-800 no-underline">{val(c.after)}</ins>
        </li>
      ))}
    </ul>
  );
}

function DiffView({ diff }: { diff: ProfileDiff }) {
  if (diff.empty) return <p className="text-sm text-slate-500">No field differs from the version before it.</p>;
  return (
    <div className="space-y-3 text-sm">
      {diff.personal.length > 0 && (
        <div>
          <h4 className="font-medium">Personal details</h4>
          <FieldChanges changes={diff.personal} />
        </div>
      )}
      {diff.sections.map((d) => (
        <div key={d.key}>
          <h4 className="font-medium">{d.title}</h4>
          <ul className="space-y-1">
            {d.added.map((l, i) => (
              <li key={`a${i}`} className="text-green-800">
                + added {l || "(blank row)"}
              </li>
            ))}
            {d.removed.map((l, i) => (
              <li key={`r${i}`} className="text-red-700">
                − removed {l || "(blank row)"}
              </li>
            ))}
            {d.changed.map((c, i) => (
              <li key={`c${i}`}>
                <span className="text-amber-800">~ changed {c.label}</span>
                <div className="pl-4">
                  <FieldChanges changes={c.fields} />
                </div>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function Snapshot({ profile }: { profile: ProfileFull }) {
  const p = profile.personal_info as Record<string, unknown> | null;
  return (
    <details className="rounded border border-slate-200 bg-white p-4">
      <summary className="cursor-pointer font-medium">Full contents of version {profile.version.version_number}</summary>
      <div className="mt-3 space-y-4 text-sm">
        <div>
          <h4 className="font-medium">Personal details</h4>
          {!p ? (
            <p className="text-slate-500">No personal section in this version.</p>
          ) : (
            <dl className="grid grid-cols-[minmax(8rem,12rem)_1fr] gap-x-3 gap-y-0.5">
              {PERSONAL_FIELDS.map((k) => (
                <div key={k} className="contents">
                  <dt className="text-slate-500">{PERSONAL_LABELS[k]}</dt>
                  <dd className="break-words">{p[k] == null ? <span className="text-slate-400">not answered</span> : display(p[k])}</dd>
                </div>
              ))}
            </dl>
          )}
        </div>
        {SECTIONS.map((sp) => {
          const rows = sectionRows(profile, sp.key);
          return (
            <div key={sp.key}>
              <h4 className="font-medium">
                {sp.title} <span className="font-normal text-slate-500">({rows.length})</span>
              </h4>
              {!rows.length && <p className="text-slate-500">none</p>}
              <ul className="space-y-1">
                {rows.map((r, i) => (
                  <li key={String(r.id ?? i)} className="rounded bg-slate-50 px-2 py-1">
                    <span className="font-medium">{sp.label(r) || "(blank)"}</span>
                    <span className="block text-xs text-slate-600">
                      {sp.fields
                        .filter((f) => r[f.name] != null && r[f.name] !== "")
                        .map((f) => `${f.label}: ${display(r[f.name])}`)
                        .join(" · ")}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </div>
    </details>
  );
}
