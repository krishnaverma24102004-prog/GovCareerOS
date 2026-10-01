"use client";
/**
 * Edits every section of the current profile and saves them with one PATCH /profile.
 *
 * Only sections that differ from the loaded version are sent; the backend copies the others into the new
 * immutable version. A 422 from the API is mapped back onto the field it names; anything that cannot be
 * placed on a field is listed above the save button.
 */
import { useMemo, useState, type FormEvent } from "react";

import { ErrorNote } from "@/components/ui";
import type { ListSectionKey, PersonalInfoFull, ProfileFull, ProfilePatch } from "@/lib/types-profile-editor";

import { PersonalEditor } from "./PersonalEditor";
import { ProfileSaveError, issuePath, patchProfile } from "./request";
import { SectionEditor } from "./SectionEditor";
import { PERSONAL_FIELDS, SECTIONS, pickPersonal, sectionRows, toFormRows, toPayloadRows, type FormRow } from "./sections";
import type { FieldErrors } from "./fields";

type RowsBySection = Record<ListSectionKey, FormRow[]>;

function initialRows(profile: ProfileFull | null): RowsBySection {
  return Object.fromEntries(SECTIONS.map((sp) => [sp.key, toFormRows(sp, sectionRows(profile, sp.key))])) as RowsBySection;
}

function personalPayload(p: PersonalInfoFull): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const k of PERSONAL_FIELDS) {
    const v = p[k];
    out[k] = typeof v === "string" ? (v.trim() === "" ? null : v.trim()) : v ?? null;
  }
  return out;
}

/** Which error paths have a place on the form, so the rest can be listed separately. */
function placedPaths(rows: RowsBySection): Set<string> {
  const placed = new Set<string>(["personal_info"]);
  for (const k of PERSONAL_FIELDS) placed.add(`personal_info.${k}`);
  for (const sp of SECTIONS) {
    placed.add(sp.key);
    rows[sp.key].forEach((_, i) => {
      placed.add(`${sp.key}.${i}`);
      for (const f of sp.fields) placed.add(`${sp.key}.${i}.${f.name}`);
    });
  }
  return placed;
}

export function ProfileEditor({ current, onSaved }: { current: ProfileFull | null; onSaved: (p: ProfileFull) => void }) {
  const startPersonal = useMemo(() => pickPersonal(current?.personal_info), [current]);
  const startRows = useMemo(() => initialRows(current), [current]);
  const startJson = useMemo(
    () => ({
      personal: JSON.stringify(personalPayload(startPersonal)),
      ...Object.fromEntries(SECTIONS.map((sp) => [sp.key, JSON.stringify(toPayloadRows(sp, startRows[sp.key]))])),
    }) as Record<"personal" | ListSectionKey, string>,
    [startPersonal, startRows],
  );

  const [personal, setPersonal] = useState<PersonalInfoFull>(startPersonal);
  const [rows, setRows] = useState<RowsBySection>(startRows);
  const [summary, setSummary] = useState("");
  const [errors, setErrors] = useState<FieldErrors>({});
  const [unplaced, setUnplaced] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const personalDirty = JSON.stringify(personalPayload(personal)) !== startJson.personal;
  const dirtySections = SECTIONS.filter((sp) => JSON.stringify(toPayloadRows(sp, rows[sp.key])) !== startJson[sp.key]);
  const anyDirty = personalDirty || dirtySections.length > 0;

  function clearErrors(prefix: string) {
    setErrors((prev) => Object.fromEntries(Object.entries(prev).filter(([k]) => k !== prefix && !k.startsWith(`${prefix}.`))));
  }

  function setSection(key: ListSectionKey, next: FormRow[], structural: boolean) {
    setRows((prev) => ({ ...prev, [key]: next }));
    if (structural) clearErrors(key);
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!anyDirty) return;
    setBusy(true);
    setError(null);
    setErrors({});
    setUnplaced([]);
    const body: ProfilePatch = { change_summary: summary.trim() || null };
    if (personalDirty) body.personal_info = personalPayload(personal);
    for (const sp of dirtySections) body[sp.key] = toPayloadRows(sp, rows[sp.key]);
    try {
      const saved = await patchProfile(body);
      onSaved(saved);
    } catch (err) {
      if (err instanceof ProfileSaveError && err.issues.length) {
        const placed = placedPaths(rows);
        const map: FieldErrors = {};
        const rest: string[] = [];
        for (const issue of err.issues) {
          const path = issuePath(issue);
          if (placed.has(path)) (map[path] ??= []).push(issue.msg);
          else rest.push(`${path || "request"}: ${issue.msg}`);
        }
        setErrors(map);
        setUnplaced(rest);
      }
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  function discardAll() {
    setPersonal(startPersonal);
    setRows(startRows);
    setErrors({});
    setUnplaced([]);
    setError(null);
  }

  const changedNames = [personalDirty ? "personal details" : null, ...dirtySections.map((sp) => sp.title.toLowerCase())].filter(Boolean);

  return (
    <form onSubmit={submit} noValidate className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-medium">Edit profile</h2>
        <p className="text-xs text-slate-500">
          {current ? `Editing from version ${current.version.version_number}. ` : "No profile yet: saving creates version 1. "}
          Saving creates a new version; earlier versions are never changed.
        </p>
      </div>

      <PersonalEditor
        value={personal}
        dirty={personalDirty}
        errors={errors}
        onChange={setPersonal}
        onReset={() => {
          setPersonal(startPersonal);
          clearErrors("personal_info");
        }}
      />

      {SECTIONS.map((sp) => (
        <SectionEditor
          key={sp.key}
          spec={sp}
          rows={rows[sp.key]}
          dirty={dirtySections.includes(sp)}
          errors={errors}
          onChange={(next, structural) => setSection(sp.key, next, structural)}
          onReset={() => setSection(sp.key, startRows[sp.key], true)}
        />
      ))}

      <div className="sticky bottom-0 space-y-2 rounded border border-slate-200 bg-white p-4 shadow-sm">
        <ErrorNote error={error} />
        {unplaced.length > 0 && (
          <ul className="list-disc pl-5 text-sm text-red-800">
            {unplaced.map((m, i) => (
              <li key={i}>{m}</li>
            ))}
          </ul>
        )}
        <label className="block text-sm">
          What changed (optional, stored with the version)
          <input className="w-full rounded border border-slate-300 px-2 py-1" value={summary} onChange={(e) => setSummary(e.target.value)} />
        </label>
        <div className="flex flex-wrap items-center gap-3">
          <button disabled={busy || !anyDirty} className="rounded bg-blue-600 px-4 py-2 text-white disabled:opacity-50">
            {busy ? "Saving…" : "Save new version"}
          </button>
          {anyDirty ? (
            <>
              <span className="text-sm text-slate-600">Will send: {changedNames.join(", ")}</span>
              <button type="button" onClick={discardAll} className="text-sm text-slate-600 hover:underline">
                Discard all changes
              </button>
            </>
          ) : (
            <span className="text-sm text-slate-500">No changes yet.</span>
          )}
        </div>
      </div>
    </form>
  );
}
