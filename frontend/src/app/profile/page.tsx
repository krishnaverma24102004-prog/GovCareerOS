"use client";
/**
 * Master profile: the current version, every earlier version (read-only), and an edit form for the
 * personal section including the relaxation questions the eligibility engine needs.
 *
 * Saving PATCHes only `personal_info`, so the backend carries every other section over into the new
 * immutable version; the whole personal section is sent because a sent section replaces the old one.
 */
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { Badge, ErrorNote, Shell, errText, fmtDate, useSession } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import type { Citizenship, PersonalInfo, Profile, VersionSummary } from "@/lib/types";

const PERSONAL_KEYS: (keyof PersonalInfo)[] = [
  "full_name", "date_of_birth", "gender", "category", "is_pwbd", "disability_percentage", "disability_types",
  "citizenship_status", "is_ex_serviceman", "armed_forces_service_months", "is_central_govt_employee",
  "central_govt_service_start", "career_interests", "nationality", "contact", "address", "preferred_locations",
];

const CITIZENSHIP: [Citizenship, string][] = [
  ["INDIAN_CITIZEN", "Citizen of India"],
  ["NEPAL_SUBJECT", "Subject of Nepal"],
  ["BHUTAN_SUBJECT", "Subject of Bhutan"],
  ["TIBETAN_REFUGEE_PRE_1962", "Tibetan refugee who came before 1 Jan 1962"],
  ["PIO_MIGRANT", "Person of Indian origin who migrated with intent to settle"],
  ["OTHER", "Other"],
];

function pickPersonal(p: PersonalInfo | null | undefined): PersonalInfo {
  const out: PersonalInfo = {};
  for (const k of PERSONAL_KEYS) (out as Record<string, unknown>)[k] = p?.[k] ?? null;
  return out;
}

export default function ProfilePage() {
  const user = useSession();
  return <Shell user={user}>{user && <ProfileView />}</Shell>;
}

function ProfileView() {
  const [current, setCurrent] = useState<Profile | null>(null);
  const [viewed, setViewed] = useState<Profile | null>(null);
  const [versions, setVersions] = useState<VersionSummary[]>([]);
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      const [p, vs] = await Promise.all([
        api<Profile>("/profile").catch((e) => {
          if (e instanceof ApiError && e.status === 404) return null;
          throw e;
        }),
        api<VersionSummary[]>("/profile/versions"),
      ]);
      setCurrent(p);
      setViewed(p);
      setMissing(p === null);
      setVersions([...vs].sort((a, b) => b.version_number - a.version_number));
    } catch (e) {
      setError(errText(e));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function openVersion(id: string) {
    try {
      setViewed(await api<Profile>(`/profile/versions/${id}`));
    } catch (e) {
      setError(errText(e));
    }
  }

  async function save(personal: PersonalInfo, summary: string) {
    setError(null);
    setSaved(null);
    try {
      const p = await api<Profile>("/profile", {
        method: "PATCH",
        body: { personal_info: personal, change_summary: summary || null },
      });
      setSaved(p.version.version_number);
      await load();
    } catch (e) {
      setError(errText(e));
    }
  }

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Profile</h1>
      <ErrorNote error={error} />
      {missing && <p className="text-slate-600">No profile yet. Saving the form below creates version 1.</p>}
      {saved !== null && (
        <p className="rounded border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-800">Saved as version {saved}.</p>
      )}

      <div className="grid gap-6 md:grid-cols-[1fr_16rem]">
        <section>{viewed && <ProfileSummary profile={viewed} isCurrent={viewed.version.id === current?.version.id} />}</section>
        <aside>
          <h2 className="mb-2 font-medium">Versions</h2>
          <ul className="space-y-1 text-sm">
            {versions.map((v) => (
              <li key={v.id}>
                <button
                  onClick={() => openVersion(v.id)}
                  className={`w-full rounded px-2 py-1 text-left hover:bg-slate-100 ${viewed?.version.id === v.id ? "bg-slate-200" : ""}`}
                >
                  <span className="font-medium">v{v.version_number}</span> · {fmtDate(v.created_at)}
                  {v.change_summary && <span className="block truncate text-slate-500">{v.change_summary}</span>}
                </button>
              </li>
            ))}
          </ul>
        </aside>
      </div>

      {(current || missing) && <PersonalForm key={current?.version.id ?? "new"} initial={pickPersonal(current?.personal_info)} onSave={save} />}
    </div>
  );
}

function ProfileSummary({ profile, isCurrent }: { profile: Profile; isCurrent: boolean }) {
  const p = profile.personal_info;
  const yn = (v: boolean | null | undefined) => (v == null ? "not answered" : v ? "yes" : "no");
  const rows: [string, string][] = p
    ? [
        ["Name", p.full_name || "—"],
        ["Date of birth", p.date_of_birth || "—"],
        ["Category", p.category || "—"],
        ["Citizenship", p.citizenship_status || "not answered"],
        ["PwBD", `${yn(p.is_pwbd)}${p.is_pwbd ? ` (${p.disability_percentage ?? "?"}%${p.disability_types ? `, ${p.disability_types}` : ""})` : ""}`],
        ["Ex-serviceman", `${yn(p.is_ex_serviceman)}${p.is_ex_serviceman && p.armed_forces_service_months != null ? ` (${p.armed_forces_service_months} months)` : ""}`],
        ["Central govt employee", `${yn(p.is_central_govt_employee)}${p.central_govt_service_start ? ` since ${p.central_govt_service_start}` : ""}`],
        ["Preferred locations", p.preferred_locations || "—"],
        ["Career interests", p.career_interests || "—"],
      ]
    : [];
  return (
    <div className="rounded border border-slate-200 bg-white p-4">
      <h2 className="mb-3 font-medium">
        Version {profile.version.version_number} {isCurrent ? <Badge value="CURRENT" /> : <Badge value="EARLIER VERSION" />}
      </h2>
      {!p && <p className="text-sm text-slate-500">No personal section in this version.</p>}
      <dl className="grid grid-cols-[10rem_1fr] gap-x-3 gap-y-1 text-sm">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-slate-500">{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
      <h3 className="mt-4 font-medium">Education</h3>
      <ul className="list-disc pl-5 text-sm">
        {profile.education.map((e) => (
          <li key={e.id}>
            {e.degree}
            {e.specialization ? ` (${e.specialization})` : ""} — {e.institution}
            {e.end_date ? `, ${e.end_date}` : ""}
          </li>
        ))}
        {!profile.education.length && <li className="text-slate-500">none</li>}
      </ul>
      <h3 className="mt-3 font-medium">Experience</h3>
      <ul className="list-disc pl-5 text-sm">
        {profile.experience.map((x) => (
          <li key={x.id}>
            {x.designation}, {x.organization} ({x.start_date || "?"} – {x.end_date || "present"})
          </li>
        ))}
        {!profile.experience.length && <li className="text-slate-500">none</li>}
      </ul>
      {profile.skills.length > 0 && (
        <p className="mt-3 text-sm">
          <span className="font-medium">Skills:</span> {profile.skills.map((s) => s.skill_name).join(", ")}
        </p>
      )}
      <p className="mt-3 text-xs text-slate-500">
        Education, experience and other sections are edited through the API (PATCH /profile); this form edits the personal section.
      </p>
    </div>
  );
}

type Tri = "" | "yes" | "no";
const toTri = (v: boolean | null | undefined): Tri => (v == null ? "" : v ? "yes" : "no");
const fromTri = (v: Tri): boolean | null => (v === "" ? null : v === "yes");
const numOrNull = (v: string): number | null => (v.trim() === "" ? null : Number(v));
const strOrNull = (v: string): string | null => (v.trim() === "" ? null : v.trim());

function PersonalForm({ initial, onSave }: { initial: PersonalInfo; onSave: (p: PersonalInfo, summary: string) => Promise<void> }) {
  const [f, setF] = useState<PersonalInfo>(initial);
  const [summary, setSummary] = useState("");
  const [busy, setBusy] = useState(false);
  const set = <K extends keyof PersonalInfo>(k: K, v: PersonalInfo[K]) => setF((prev) => ({ ...prev, [k]: v }));

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    await onSave(f, summary);
    setBusy(false);
  }

  const input = "w-full rounded border border-slate-300 px-2 py-1";
  const text = (k: keyof PersonalInfo, label: string, type = "text") => (
    <label className="block text-sm">
      {label}
      <input
        className={input}
        type={type}
        value={(f[k] as string | number | null | undefined) ?? ""}
        onChange={(e) => set(k, (type === "number" ? numOrNull(e.target.value) : strOrNull(e.target.value)) as never)}
      />
    </label>
  );
  const question = (k: "is_pwbd" | "is_ex_serviceman" | "is_central_govt_employee", label: string) => (
    <label className="block text-sm">
      {label}
      <select className={input} value={toTri(f[k])} onChange={(e) => set(k, fromTri(e.target.value as Tri))}>
        <option value="">Not answered</option>
        <option value="yes">Yes</option>
        <option value="no">No</option>
      </select>
    </label>
  );

  return (
    <form onSubmit={submit} className="space-y-4 rounded border border-slate-200 bg-white p-4">
      <h2 className="font-medium">Edit personal details (creates a new version)</h2>
      <div className="grid gap-3 md:grid-cols-2">
        {text("full_name", "Full name")}
        {text("date_of_birth", "Date of birth", "date")}
        {text("gender", "Gender")}
        <label className="block text-sm">
          Category
          <select className={input} value={f.category ?? ""} onChange={(e) => set("category", strOrNull(e.target.value))}>
            <option value="">Not answered</option>
            {["UR", "EWS", "OBC", "SC", "ST"].map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
      </div>

      <fieldset className="grid gap-3 border-t border-slate-200 pt-3 md:grid-cols-2">
        <legend className="text-sm font-medium">Relaxation questions</legend>
        <label className="block text-sm">
          Citizenship
          <select
            className={input}
            value={f.citizenship_status ?? ""}
            onChange={(e) => set("citizenship_status", (e.target.value || null) as Citizenship | null)}
          >
            <option value="">Not answered</option>
            {CITIZENSHIP.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </label>
        {question("is_pwbd", "Person with benchmark disability (PwBD)?")}
        {f.is_pwbd && text("disability_percentage", "Disability percentage", "number")}
        {f.is_pwbd && text("disability_types", "Disability sub-category codes (e.g. OL or LV,HH)")}
        {question("is_ex_serviceman", "Ex-serviceman?")}
        {f.is_ex_serviceman && text("armed_forces_service_months", "Armed forces service (months)", "number")}
        {question("is_central_govt_employee", "Central Government employee?")}
        {f.is_central_govt_employee && text("central_govt_service_start", "Continuous service since", "date")}
      </fieldset>

      <fieldset className="grid gap-3 border-t border-slate-200 pt-3 md:grid-cols-2">
        <legend className="text-sm font-medium">Preferences</legend>
        {text("preferred_locations", "Preferred locations (comma separated)")}
        {text("career_interests", "Career interests (comma separated)")}
      </fieldset>

      <label className="block text-sm">
        What changed (optional)
        <input className={input} value={summary} onChange={(e) => setSummary(e.target.value)} />
      </label>
      <button disabled={busy} className="rounded bg-blue-600 px-4 py-2 text-white disabled:opacity-50">
        {busy ? "Saving…" : "Save new version"}
      </button>
    </form>
  );
}
