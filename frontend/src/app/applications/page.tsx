"use client";
/**
 * Application tracker: the caller's applications (newest first, as GET /applications returns them),
 * tracked against a stored vacancy, a stored exam (exam_id) or an exam named in free text (exam_ref). Each one shows its status with the full status
 * history, the application number, dates and notes; withdrawn applications are listed separately.
 *
 * Status changes go through PATCH { status, status_note } so the backend records the history row;
 * the other fields are PATCHed only when they changed. A vacancy application links to its
 * preparation checklist (/applications/prep), personalised by the profile version it is linked to; a stored
 * exam links to its apply-ready checklist (/apply-ready/exam/{id}).
 */
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";

import { Badge, ErrorNote, Shell, errText, fmtDate, useSession } from "@/components/ui";
import { Pill, fromLocalInput, safeHref, toLocalInput } from "@/components/applications";
import { api, ApiError } from "@/lib/api";
import type { Profile, VersionSummary } from "@/lib/types";
import {
  APPLICATION_STATUSES,
  type Application,
  type ApplicationCreate,
  type ApplicationPatch,
  type ApplicationStatus,
  type ExamSummary,
  type VacancySummary,
} from "@/lib/types-applications";

export default function ApplicationsPage() {
  const user = useSession();
  return <Shell user={user}>{user && <ApplicationsView />}</Shell>;
}

type VacancyInfo = { ok: true; v: VacancySummary } | { ok: false; error: string };

function ApplicationsView() {
  const [apps, setApps] = useState<Application[] | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [versions, setVersions] = useState<Record<string, number>>({});
  const [vacancies, setVacancies] = useState<Record<string, VacancyInfo>>({});
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      setApps(await api<Application[]>("/applications"));
    } catch (e) {
      setError(errText(e));
    }
  }, []);

  useEffect(() => {
    load();
    api<Profile>("/profile")
      .then(setProfile)
      .catch((e) => {
        if (!(e instanceof ApiError && e.status === 404)) setError(errText(e));
      });
    api<VersionSummary[]>("/profile/versions")
      .then((vs) => setVersions(Object.fromEntries(vs.map((v) => [v.id, v.version_number]))))
      .catch(() => undefined); // only used to label the linked version
  }, [load]);

  // Post name / advertisement number for every tracked vacancy (fetched once per id)
  useEffect(() => {
    if (!apps) return;
    const missing = [...new Set(apps.map((a) => a.vacancy_id).filter((id): id is string => !!id))].filter((id) => !(id in vacancies));
    if (!missing.length) return;
    let live = true;
    Promise.all(
      missing.map((id) =>
        api<VacancySummary>(`/vacancies/${id}`)
          .then((v): [string, VacancyInfo] => [id, { ok: true, v }])
          .catch((e): [string, VacancyInfo] => [id, { ok: false, error: errText(e) }]),
      ),
    ).then((rows) => {
      if (live) setVacancies((prev) => ({ ...prev, ...Object.fromEntries(rows) }));
    });
    return () => {
      live = false;
    };
  }, [apps, vacancies]);

  const replace = (a: Application) => setApps((prev) => prev?.map((x) => (x.id === a.id ? a : x)) ?? prev);
  const remove = (id: string) => setApps((prev) => prev?.filter((x) => x.id !== id) ?? prev);

  const active = useMemo(() => apps?.filter((a) => a.status !== "WITHDRAWN") ?? [], [apps]);
  const withdrawn = useMemo(() => apps?.filter((a) => a.status === "WITHDRAWN") ?? [], [apps]);
  // Vacancy and exam ids already tracked (UUIDs never collide across the two)
  const tracked = useMemo(() => new Set(apps?.flatMap((a) => [a.vacancy_id, a.exam_id]).filter(Boolean) as string[]), [apps]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">Applications</h1>
        {apps && <span className="text-sm text-slate-500">{active.length} active · {withdrawn.length} withdrawn</span>}
        <button
          onClick={() => setShowCreate((s) => !s)}
          className="ml-auto rounded bg-blue-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-800"
        >
          {showCreate ? "Close" : "Track an application"}
        </button>
      </div>
      <ErrorNote error={error} />

      {showCreate && (
        <CreateForm
          profile={profile}
          tracked={tracked}
          onCreated={(a) => {
            setApps((prev) => [a, ...(prev ?? [])]);
            setShowCreate(false);
          }}
        />
      )}

      {apps === null && !error && <p className="text-slate-500">Loading applications…</p>}
      {apps?.length === 0 && (
        <p className="rounded border border-dashed border-slate-300 bg-white p-4 text-sm text-slate-600">
          You are not tracking any application yet. Use <span className="font-medium">Track an application</span> to add a
          vacancy from the database or an exam such as an SSC or UPSC examination.
        </p>
      )}

      {active.length > 0 && (
        <section className="space-y-3">
          {active.map((a) => (
            <ApplicationCard key={a.id} app={a} vacancy={a.vacancy_id ? vacancies[a.vacancy_id] : undefined} versions={versions} onChange={replace} onDelete={remove} />
          ))}
        </section>
      )}
      {apps && apps.length > 0 && active.length === 0 && <p className="text-sm text-slate-500">No active applications.</p>}

      {withdrawn.length > 0 && (
        <section className="space-y-3">
          <h2 className="border-t border-slate-200 pt-4 font-medium text-slate-600">Withdrawn ({withdrawn.length})</h2>
          {withdrawn.map((a) => (
            <ApplicationCard key={a.id} app={a} vacancy={a.vacancy_id ? vacancies[a.vacancy_id] : undefined} versions={versions} onChange={replace} onDelete={remove} />
          ))}
        </section>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- create ---------------------------------------------------------------- */

const input = "w-full rounded border border-slate-300 px-2 py-1 text-sm";

function CreateForm({ profile, tracked, onCreated }: { profile: Profile | null; tracked: Set<string>; onCreated: (a: Application) => void }) {
  const [kind, setKind] = useState<"vacancy" | "exam">("vacancy");
  const [query, setQuery] = useState("");
  const [vacancyResults, setVacancyResults] = useState<VacancySummary[] | null>(null);
  const [examResults, setExamResults] = useState<ExamSummary[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [vacancy, setVacancy] = useState<VacancySummary | null>(null);
  // A stored exam chosen from the search (sent as exam_id); free text alone is sent as exam_ref
  const [exam, setExam] = useState<ExamSummary | null>(null);
  const [examRef, setExamRef] = useState("");
  const [status, setStatus] = useState<ApplicationStatus>("PLANNED");
  const [applicationNumber, setApplicationNumber] = useState("");
  const [notes, setNotes] = useState("");
  const [linkProfile, setLinkProfile] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function search(e: FormEvent) {
    e.preventDefault();
    setSearching(true);
    setSearchError(null);
    const q = encodeURIComponent(query.trim());
    try {
      if (kind === "vacancy") setVacancyResults(await api<VacancySummary[]>(`/vacancies?limit=25${q ? `&q=${q}` : ""}`));
      else setExamResults(await api<ExamSummary[]>(`/exams?limit=25${q ? `&q=${q}` : ""}`));
    } catch (err) {
      setSearchError(errText(err));
    } finally {
      setSearching(false);
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    const body: ApplicationCreate = {
      status,
      application_number: applicationNumber.trim() || null,
      notes: notes.trim() || null,
      profile_version_id: linkProfile && profile ? profile.version.id : null,
    };
    if (kind === "vacancy") {
      if (!vacancy) return setError("Choose a vacancy first.");
      body.vacancy_id = vacancy.id;
    } else {
      if (!exam && !examRef.trim()) return setError("Enter or choose an exam.");
      if (exam) body.exam_id = exam.id;
      if (examRef.trim()) body.exam_ref = examRef.trim();
    }
    setBusy(true);
    try {
      onCreated(await api<Application>("/applications", { method: "POST", body }));
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="space-y-4 rounded border border-slate-200 bg-white p-4">
      <div className="flex gap-2 text-sm">
        {(["vacancy", "exam"] as const).map((k) => (
          <button
            key={k}
            type="button"
            onClick={() => {
              setKind(k);
              setError(null);
              setSearchError(null);
            }}
            className={`rounded px-3 py-1 ${kind === k ? "bg-slate-800 text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200"}`}
          >
            {k === "vacancy" ? "From a vacancy" : "From an exam"}
          </button>
        ))}
      </div>

      <form onSubmit={search} className="flex gap-2">
        <input
          className={input}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={kind === "vacancy" ? "Search post name, ministry or department" : "Search exam name or code (e.g. CHSL)"}
        />
        <button className="rounded bg-slate-100 px-3 py-1 text-sm hover:bg-slate-200" disabled={searching}>
          {searching ? "Searching…" : "Search"}
        </button>
      </form>
      <ErrorNote error={searchError} />

      {kind === "vacancy" && vacancyResults && (
        <ul className="max-h-72 space-y-1 overflow-y-auto text-sm">
          {vacancyResults.length === 0 && <li className="text-slate-500">No vacancies match.</li>}
          {vacancyResults.map((v) => {
            const already = tracked.has(v.id);
            return (
              <li key={v.id}>
                <label className={`flex gap-2 rounded px-2 py-1 ${vacancy?.id === v.id ? "bg-blue-50" : "hover:bg-slate-50"} ${already ? "opacity-60" : ""}`}>
                  <input type="radio" name="vacancy" disabled={already} checked={vacancy?.id === v.id} onChange={() => setVacancy(v)} />
                  <span>
                    <span className="font-medium">{v.post_name}</span>
                    <span className="block text-xs text-slate-500">
                      {[v.organization, v.department, v.advertisement_number && `Advt ${v.advertisement_number}`].filter(Boolean).join(" · ")}
                      {" · last date "}
                      {fmtDate(v.application_deadline)}
                      {already && " · already tracked"}
                    </span>
                  </span>
                </label>
              </li>
            );
          })}
        </ul>
      )}

      {kind === "exam" && (
        <>
          {examResults && (
            <ul className="max-h-72 space-y-1 overflow-y-auto text-sm">
              {examResults.length === 0 && <li className="text-slate-500">No exams match. You can still type the exam name below.</li>}
              {examResults.map((x) => {
                const already = tracked.has(x.id);
                return (
                  <li key={x.id}>
                    <label className={`flex gap-2 rounded px-2 py-1 ${exam?.id === x.id ? "bg-blue-50" : "hover:bg-slate-50"} ${already ? "opacity-60" : ""}`}>
                      <input
                        type="radio"
                        name="exam"
                        disabled={already}
                        checked={exam?.id === x.id}
                        onChange={() => {
                          setExam(x);
                          setExamRef(examLabel(x));
                        }}
                      />
                      <span>
                        <span className="font-medium">{x.name}</span>
                        <span className="block text-xs text-slate-500">
                          {x.conducting_body} · <Badge value={x.status} /> · applications close {fmtDate(x.application_end)}
                          {already && " · already tracked"}
                        </span>
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>
          )}
          <label className="block text-sm">
            <span className="text-slate-600">{exam ? "Label for this exam" : "Exam (not in the database)"}</span>
            <input className={input} maxLength={255} value={examRef} onChange={(e) => setExamRef(e.target.value)} placeholder="e.g. UPSC CSE 2027" />
          </label>
          <p className="text-sm">
            <span className="text-slate-600">Stored exam: </span>
            {exam ? (
              <>
                <span className="font-medium">{exam.name}</span>{" "}
                <button type="button" className="text-blue-700 underline" onClick={() => setExam(null)}>
                  clear
                </button>
                <span className="block text-xs text-slate-500">Official changes to this exam are alerted and it gets an apply-ready checklist.</span>
              </>
            ) : (
              <span className="text-slate-500">none chosen — search above, or type a name to track it as free text</span>
            )}
          </p>
        </>
      )}

      <form onSubmit={submit} className="space-y-3">
        {kind === "vacancy" && (
          <p className="text-sm">
            <span className="text-slate-600">Vacancy: </span>
            {vacancy ? <span className="font-medium">{vacancy.post_name}</span> : <span className="text-slate-500">none chosen — search above</span>}
          </p>
        )}
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block text-sm">
            <span className="text-slate-600">Status</span>
            <select className={input} value={status} onChange={(e) => setStatus(e.target.value as ApplicationStatus)}>
              {APPLICATION_STATUSES.filter((s) => s !== "WITHDRAWN").map((s) => (
                <option key={s} value={s}>
                  {s.replace(/_/g, " ")}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-sm">
            <span className="text-slate-600">Application / registration number</span>
            <input className={input} maxLength={100} value={applicationNumber} onChange={(e) => setApplicationNumber(e.target.value)} />
          </label>
        </div>
        <label className="block text-sm">
          <span className="text-slate-600">Notes</span>
          <textarea className={input} rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
        </label>
        {profile && (
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={linkProfile} onChange={(e) => setLinkProfile(e.target.checked)} />
            Link to profile version {profile.version.version_number} (used for the preparation checklist)
          </label>
        )}
        <ErrorNote error={error} />
        <button className="rounded bg-blue-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-800 disabled:opacity-50" disabled={busy}>
          {busy ? "Saving…" : "Track"}
        </button>
      </form>
    </section>
  );
}

function examLabel(x: ExamSummary): string {
  const year = x.exam_year ? String(x.exam_year) : "";
  return year && !x.name.includes(year) ? `${x.name} ${year}` : x.name;
}

/* ---------------------------------------------------------------- one application ---------------------------------------------------------------- */

function ApplicationCard({
  app,
  vacancy,
  versions,
  onChange,
  onDelete,
}: {
  app: Application;
  vacancy: VacancyInfo | undefined;
  versions: Record<string, number>;
  onChange: (a: Application) => void;
  onDelete: (id: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function patch(body: ApplicationPatch): Promise<boolean> {
    setError(null);
    try {
      onChange(await api<Application>(`/applications/${app.id}`, { method: "PATCH", body }));
      return true;
    } catch (e) {
      setError(errText(e));
      return false;
    }
  }

  async function del() {
    if (!window.confirm("Delete this application and its status history? This cannot be undone.")) return;
    setError(null);
    try {
      await api(`/applications/${app.id}`, { method: "DELETE" });
      onDelete(app.id);
    } catch (e) {
      setError(errText(e));
    }
  }

  const v = vacancy?.ok ? vacancy.v : null;
  const pv = app.profile_version_id ? `profile_version_id=${app.profile_version_id}` : "";
  const prepHref = app.vacancy_id ? `/applications/prep?vacancy_id=${app.vacancy_id}${pv ? `&${pv}` : ""}` : null;
  const applyReadyHref = app.vacancy_id
    ? `/apply-ready/vacancy/${app.vacancy_id}${pv ? `?${pv}` : ""}`
    : app.exam_id
      ? `/apply-ready/exam/${app.exam_id}${pv ? `?${pv}` : ""}`
      : null;
  const history = [...app.history].sort((a, b) => b.changed_at.localeCompare(a.changed_at));
  const withdrawn = app.status === "WITHDRAWN";

  return (
    <article className={`rounded border p-4 text-sm ${withdrawn ? "border-slate-200 bg-slate-50" : "border-slate-200 bg-white"}`}>
      <div className="flex flex-wrap items-start gap-2">
        <div className="min-w-0 flex-1">
          {app.vacancy_id ? (
            <>
              <Link href={`/vacancies/${app.vacancy_id}`} className="font-medium text-blue-700 hover:underline">
                {v ? v.post_name : vacancy && !vacancy.ok ? `Vacancy (${vacancy.error})` : "Vacancy…"}
              </Link>
              {v && (
                <p className="text-xs text-slate-500">
                  {[v.organization, v.department, v.advertisement_number && `Advt ${v.advertisement_number}`].filter(Boolean).join(" · ")}
                  {" · last date "}
                  {fmtDate(v.application_deadline, true)}
                  {safeHref(v.notification_url) && (
                    <>
                      {" · "}
                      <a className="text-blue-700 underline" href={safeHref(v.notification_url)!} target="_blank" rel="noreferrer">
                        official notification
                      </a>
                    </>
                  )}
                </p>
              )}
            </>
          ) : app.exam_id ? (
            <p>
              <Link href={`/exams/${app.exam_id}`} className="font-medium text-blue-700 hover:underline">
                {app.exam_ref || "Exam"}
              </Link>{" "}
              <span className="text-xs text-slate-500">(stored exam)</span>
            </p>
          ) : (
            <p className="font-medium">
              {app.exam_ref} <span className="text-xs font-normal text-slate-500">(exam)</span>
            </p>
          )}
        </div>
        <Pill value={app.status} />
      </div>

      <dl className="mt-3 grid grid-cols-[10rem_1fr] gap-x-3 gap-y-1">
        <dt className="text-slate-500">Application number</dt>
        <dd className="font-mono">{app.application_number || "—"}</dd>
        {app.applied_at && (
          <>
            <dt className="text-slate-500">Applied</dt>
            <dd>{fmtDate(app.applied_at, true)}</dd>
          </>
        )}
        {app.fee_paid_at && (
          <>
            <dt className="text-slate-500">Fee paid</dt>
            <dd>{fmtDate(app.fee_paid_at, true)}</dd>
          </>
        )}
        {app.exam_at && (
          <>
            <dt className="text-slate-500">Exam</dt>
            <dd>{fmtDate(app.exam_at, true)}</dd>
          </>
        )}
        <dt className="text-slate-500">Profile version</dt>
        <dd>{app.profile_version_id ? (versions[app.profile_version_id] ? `v${versions[app.profile_version_id]}` : "linked") : "not linked"}</dd>
        <dt className="text-slate-500">Tracked since</dt>
        <dd>{fmtDate(app.created_at)}</dd>
        {app.notes && (
          <>
            <dt className="text-slate-500">Notes</dt>
            <dd className="whitespace-pre-wrap">{app.notes}</dd>
          </>
        )}
      </dl>

      <ErrorNote error={error} />

      <div className="mt-3 flex flex-wrap gap-3">
        <StatusChanger current={app.status} onSubmit={(status, note) => patch({ status, status_note: note || null })} />
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-4 text-sm">
        {prepHref && (
          <Link href={prepHref} className="text-blue-700 underline">
            Preparation checklist
          </Link>
        )}
        {applyReadyHref && (
          <Link href={applyReadyHref} className="text-blue-700 underline">
            Apply-ready checklist
          </Link>
        )}
        <button className="text-blue-700 underline" onClick={() => setEditing((x) => !x)}>
          {editing ? "Cancel editing" : "Edit number, dates, notes"}
        </button>
        <button className="text-blue-700 underline" onClick={() => setShowHistory((x) => !x)}>
          {showHistory ? "Hide" : "Show"} status history ({app.history.length})
        </button>
        <button className="ml-auto text-red-700 underline" onClick={del}>
          Delete
        </button>
      </div>

      {editing && (
        <DetailsForm
          app={app}
          onSave={async (body) => {
            if (!Object.keys(body).length || (await patch(body))) setEditing(false);
          }}
        />
      )}

      {showHistory && (
        <ol className="mt-3 space-y-1 border-l-2 border-slate-200 pl-3">
          {history.map((h, i) => (
            <li key={`${h.changed_at}-${i}`}>
              <span className="text-slate-500">{fmtDate(h.changed_at, true)}</span>{" "}
              {h.from_status ? (
                <>
                  <Pill value={h.from_status} /> → <Pill value={h.to_status} />
                </>
              ) : (
                <>
                  created as <Pill value={h.to_status} />
                </>
              )}
              {h.note && <span className="text-slate-600"> — {h.note}</span>}
            </li>
          ))}
          {!history.length && <li className="text-slate-500">No history recorded.</li>}
        </ol>
      )}
    </article>
  );
}

function StatusChanger({ current, onSubmit }: { current: string; onSubmit: (s: ApplicationStatus, note: string) => Promise<boolean> }) {
  const [status, setStatus] = useState<ApplicationStatus>((APPLICATION_STATUSES as readonly string[]).includes(current) ? (current as ApplicationStatus) : "PLANNED");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if ((APPLICATION_STATUSES as readonly string[]).includes(current)) setStatus(current as ApplicationStatus);
  }, [current]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    if (await onSubmit(status, note.trim())) setNote("");
    setBusy(false);
  }

  return (
    <form onSubmit={submit} className="flex w-full flex-wrap items-center gap-2">
      <select className="rounded border border-slate-300 px-2 py-1 text-sm" value={status} onChange={(e) => setStatus(e.target.value as ApplicationStatus)}>
        {APPLICATION_STATUSES.map((s) => (
          <option key={s} value={s}>
            {s.replace(/_/g, " ")}
          </option>
        ))}
      </select>
      <input
        className="min-w-[12rem] flex-1 rounded border border-slate-300 px-2 py-1 text-sm"
        placeholder="Note for the history (optional)"
        value={note}
        onChange={(e) => setNote(e.target.value)}
      />
      <button className="rounded bg-slate-800 px-3 py-1 text-sm text-white hover:bg-slate-900 disabled:opacity-50" disabled={busy || status === current}>
        Change status
      </button>
    </form>
  );
}

function DetailsForm({ app, onSave }: { app: Application; onSave: (body: ApplicationPatch) => Promise<unknown> }) {
  const [applicationNumber, setApplicationNumber] = useState(app.application_number ?? "");
  const [appliedAt, setAppliedAt] = useState(toLocalInput(app.applied_at));
  const [feePaidAt, setFeePaidAt] = useState(toLocalInput(app.fee_paid_at));
  const [examAt, setExamAt] = useState(toLocalInput(app.exam_at));
  const [notes, setNotes] = useState(app.notes ?? "");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const body: ApplicationPatch = {};
    const num = applicationNumber.trim() || null;
    if (num !== (app.application_number ?? null)) body.application_number = num;
    const note = notes.trim() || null;
    if (note !== (app.notes ?? null)) body.notes = note;
    const dates: [keyof ApplicationPatch & ("applied_at" | "fee_paid_at" | "exam_at"), string][] = [
      ["applied_at", appliedAt],
      ["fee_paid_at", feePaidAt],
      ["exam_at", examAt],
    ];
    for (const [key, value] of dates) if (value !== toLocalInput(app[key])) body[key] = fromLocalInput(value);
    setBusy(true);
    await onSave(body);
    setBusy(false);
  }

  return (
    <form onSubmit={submit} className="mt-3 space-y-3 rounded border border-slate-200 bg-slate-50 p-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block">
          <span className="text-slate-600">Application / registration number</span>
          <input className={input} maxLength={100} value={applicationNumber} onChange={(e) => setApplicationNumber(e.target.value)} />
        </label>
        <label className="block">
          <span className="text-slate-600">Applied on</span>
          <input type="datetime-local" className={input} value={appliedAt} onChange={(e) => setAppliedAt(e.target.value)} />
        </label>
        <label className="block">
          <span className="text-slate-600">Fee paid on</span>
          <input type="datetime-local" className={input} value={feePaidAt} onChange={(e) => setFeePaidAt(e.target.value)} />
        </label>
        <label className="block">
          <span className="text-slate-600">Exam on</span>
          <input type="datetime-local" className={input} value={examAt} onChange={(e) => setExamAt(e.target.value)} />
        </label>
      </div>
      <label className="block">
        <span className="text-slate-600">Notes</span>
        <textarea className={input} rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} />
      </label>
      <button className="rounded bg-blue-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-800 disabled:opacity-50" disabled={busy}>
        {busy ? "Saving…" : "Save"}
      </button>
    </form>
  );
}
