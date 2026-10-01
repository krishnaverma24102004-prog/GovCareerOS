"use client";
/**
 * Preparation checklist for one vacancy (GET /applications/prep), personalised by an explicit profile
 * version: documents to upload with REQUIRED / NOT REQUIRED / IF APPLICABLE / UNKNOWN for this profile,
 * the application fee with the exemption decision, payment modes and fee rules, the accepted photo ID
 * cards and the online application (ORA) website. Every item carries its citation: the official
 * advertisement opened at the page, the section and the quoted excerpt.
 *
 * URL: /applications/prep?vacancy_id=<uuid>[&profile_version_id=<uuid>]. Without a profile version the
 * latest one is used and its number is shown; without a vacancy the page lists tracked vacancies.
 */
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { ErrorNote, Shell, errText, fmtDate, useSession } from "@/components/ui";
import { Citation, Pill, safeHref } from "@/components/applications";
import { api, ApiError } from "@/lib/api";
import type { Profile, VacancyDetail, VersionSummary } from "@/lib/types";
import type { Application, PrepChecklist, PrepDocument, PrepRule, VacancySummary } from "@/lib/types-applications";

export default function PrepChecklistPage() {
  const user = useSession();
  return (
    <Shell user={user}>
      {user && (
        <Suspense fallback={<p className="text-slate-500">Loading…</p>}>
          <PrepRoute />
        </Suspense>
      )}
    </Shell>
  );
}

function PrepRoute() {
  const params = useSearchParams();
  const vacancyId = params.get("vacancy_id");
  const versionId = params.get("profile_version_id");
  return vacancyId ? <PrepView key={`${vacancyId}:${versionId ?? ""}`} vacancyId={vacancyId} versionId={versionId} /> : <PickVacancy />;
}

/* ---------------------------------------------------------------- no vacancy chosen ---------------------------------------------------------------- */

function PickVacancy() {
  const [apps, setApps] = useState<Application[] | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Application[]>("/applications")
      .then(async (all) => {
        const withVacancy = all.filter((a) => a.vacancy_id);
        setApps(withVacancy);
        const rows = await Promise.all(
          withVacancy.map((a) =>
            api<VacancySummary>(`/vacancies/${a.vacancy_id}`)
              .then((v): [string, string] => [a.vacancy_id!, v.post_name])
              .catch((): [string, string] => [a.vacancy_id!, "Vacancy"]),
          ),
        );
        setNames(Object.fromEntries(rows));
      })
      .catch((e) => setError(errText(e)));
  }, []);

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Preparation checklist</h1>
      <p className="text-sm text-slate-600">Choose a tracked vacancy, or open the checklist from an application on the Applications page.</p>
      <ErrorNote error={error} />
      {apps === null && !error && <p className="text-slate-500">Loading…</p>}
      {apps?.length === 0 && (
        <p className="text-sm text-slate-600">
          No tracked vacancy yet. <Link className="text-blue-700 underline" href="/applications">Track one</Link> first.
        </p>
      )}
      <ul className="space-y-1 text-sm">
        {apps?.map((a) => (
          <li key={a.id}>
            <Link
              className="text-blue-700 underline"
              href={`/applications/prep?vacancy_id=${a.vacancy_id}${a.profile_version_id ? `&profile_version_id=${a.profile_version_id}` : ""}`}
            >
              {names[a.vacancy_id!] ?? "Vacancy…"}
            </Link>{" "}
            <Pill value={a.status} />
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ---------------------------------------------------------------- the checklist ---------------------------------------------------------------- */

function PrepView({ vacancyId, versionId }: { vacancyId: string; versionId: string | null }) {
  const router = useRouter();
  const pathname = usePathname();
  const [versions, setVersions] = useState<VersionSummary[] | null>(null);
  const [resolvedVersion, setResolvedVersion] = useState<string | null>(versionId);
  const [noProfile, setNoProfile] = useState(false);
  const [vacancy, setVacancy] = useState<VacancyDetail | null>(null);
  const [checklist, setChecklist] = useState<PrepChecklist | null>(null);
  const [tracked, setTracked] = useState<Application | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [trackError, setTrackError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api<VacancyDetail>(`/vacancies/${vacancyId}`)
      .then((v) => live && setVacancy(v))
      .catch((e) => live && setError(errText(e)));
    api<VersionSummary[]>("/profile/versions")
      .then((vs) => live && setVersions([...vs].sort((a, b) => b.version_number - a.version_number)))
      .catch(() => live && setVersions([]));
    api<Application[]>("/applications")
      .then((all) => live && setTracked(all.find((a) => a.vacancy_id === vacancyId) ?? null))
      .catch(() => live && setTracked(undefined));
    (async () => {
      let version = versionId;
      if (!version) {
        try {
          version = (await api<Profile>("/profile")).version.id;
        } catch (e) {
          if (!live) return;
          if (e instanceof ApiError && e.status === 404) setNoProfile(true);
          else setError(errText(e));
          return;
        }
      }
      if (!live) return;
      setResolvedVersion(version);
      try {
        const q = `vacancy_id=${encodeURIComponent(vacancyId)}&profile_version_id=${encodeURIComponent(version)}`;
        const c = await api<PrepChecklist>(`/applications/prep?${q}`);
        if (live) setChecklist(c);
      } catch (e) {
        if (live) setError(errText(e));
      }
    })();
    return () => {
      live = false;
    };
  }, [vacancyId, versionId]);

  function chooseVersion(id: string) {
    router.replace(`${pathname}?vacancy_id=${encodeURIComponent(vacancyId)}&profile_version_id=${encodeURIComponent(id)}`);
  }

  async function track() {
    setTrackError(null);
    try {
      setTracked(
        await api<Application>("/applications", {
          method: "POST",
          body: { vacancy_id: vacancyId, profile_version_id: resolvedVersion, status: "PLANNED" },
        }),
      );
    } catch (e) {
      setTrackError(errText(e));
    }
  }

  const versionNumber = versions?.find((v) => v.id === resolvedVersion)?.version_number;

  return (
    <div className="space-y-6">
      <div>
        <p className="text-sm">
          <Link className="text-blue-700 underline" href="/applications">
            ← Applications
          </Link>
        </p>
        <h1 className="mt-1 text-xl font-semibold">Preparation checklist</h1>
        {vacancy && (
          <p className="text-sm text-slate-600">
            <Link className="font-medium text-blue-700 hover:underline" href={`/vacancies/${vacancy.id}`}>
              {vacancy.post_name}
            </Link>
            {" · "}
            {vacancy.organization}
            {safeHref(vacancy.notification_url) && (
              <>
                {" · "}
                <a className="text-blue-700 underline" href={safeHref(vacancy.notification_url)!} target="_blank" rel="noreferrer">
                  official notification
                </a>
              </>
            )}
          </p>
        )}
        <div className="mt-2 flex flex-wrap items-center gap-3 text-sm">
          {versions && versions.length > 0 && (
            <label className="flex items-center gap-2">
              <span className="text-slate-600">Personalised for profile version</span>
              <select
                className="rounded border border-slate-300 px-2 py-1"
                value={resolvedVersion ?? ""}
                onChange={(e) => chooseVersion(e.target.value)}
              >
                {!resolvedVersion && <option value="">—</option>}
                {versions.map((v) => (
                  <option key={v.id} value={v.id}>
                    v{v.version_number} · {fmtDate(v.created_at)}
                    {v.id === versions[0].id ? " (latest)" : ""}
                  </option>
                ))}
              </select>
            </label>
          )}
          {versionNumber === undefined && resolvedVersion && versions && <span className="text-slate-500">profile version {resolvedVersion}</span>}
          {tracked === null && (
            <button className="rounded bg-blue-700 px-3 py-1 text-white hover:bg-blue-800" onClick={track}>
              Track this vacancy
            </button>
          )}
          {tracked && (
            <span className="text-slate-600">
              Tracked · <Pill value={tracked.status} />
            </span>
          )}
        </div>
        <ErrorNote error={trackError} />
      </div>

      {noProfile && (
        <p className="text-sm text-slate-700">
          The checklist is personalised from your profile. <Link className="text-blue-700 underline" href="/profile">Create a profile</Link> first.
        </p>
      )}
      <ErrorNote error={error} />
      {!checklist && !error && !noProfile && <p className="text-slate-500">Building the checklist…</p>}
      {checklist && <Checklist c={checklist} />}
    </div>
  );
}

const DOC_ORDER: Record<string, number> = { REQUIRED: 0, UNKNOWN: 1, IF_APPLICABLE: 2, NOT_REQUIRED: 3 };

function Checklist({ c }: { c: PrepChecklist }) {
  const docs = [...c.documents].sort((a, b) => (DOC_ORDER[a.status] ?? 9) - (DOC_ORDER[b.status] ?? 9));
  const counts = c.documents.reduce<Record<string, number>>((acc, d) => ({ ...acc, [d.status]: (acc[d.status] ?? 0) + 1 }), {});
  const notAccepted = c.upload_rules.filter((r) => r.documents?.length);
  const otherUploadRules = c.upload_rules.filter((r) => !r.documents?.length);
  const nothing = !c.portal && !c.photo_id && !c.payment && !c.fee && !c.documents.length && !c.fee_rules.length && !c.upload_rules.length;

  return (
    <div className="space-y-6">
      <section className="rounded border border-slate-200 bg-white p-4 text-sm">
        <dl className="grid grid-cols-[11rem_1fr] gap-x-3 gap-y-1">
          <dt className="text-slate-500">Advertisement no.</dt>
          <dd>{c.advertisement_number ?? "—"}</dd>
          <dt className="text-slate-500">Last date to apply</dt>
          <dd>{c.application_deadline ? fmtDate(c.application_deadline, true) : "—"}</dd>
          <dt className="text-slate-500">Documents</dt>
          <dd className="flex flex-wrap gap-2">
            {Object.keys(DOC_ORDER)
              .filter((k) => counts[k])
              .map((k) => (
                <span key={k}>
                  <Pill value={k} /> {counts[k]}
                </span>
              ))}
            {!c.documents.length && "—"}
          </dd>
        </dl>
      </section>

      {c.warnings.length > 0 && (
        <ul className="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          {c.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
      {nothing && !c.warnings.length && <p className="text-sm text-slate-500">The advertisement gave no application instructions to list.</p>}

      {c.portal && (
        <section>
          <h2 className="mb-2 font-medium">Where to apply</h2>
          <div className="rounded border border-slate-200 bg-white p-3 text-sm">
            <p>
              Online application (ORA) website:{" "}
              {safeHref(c.portal.url) ? (
                <a className="font-medium text-blue-700 underline" href={safeHref(c.portal.url)!} target="_blank" rel="noreferrer">
                  {c.portal.url}
                </a>
              ) : (
                <span className="font-mono">{c.portal.url}</span>
              )}
            </p>
            <Citation c={c.portal.citation} />
          </div>
        </section>
      )}

      {(c.fee || c.payment || c.fee_rules.length > 0) && (
        <section>
          <h2 className="mb-2 font-medium">Application fee</h2>
          <div className="space-y-3 rounded border border-slate-200 bg-white p-3 text-sm">
            {c.fee && (
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <Pill value={c.fee.status} />
                  {c.fee.amount !== null && (
                    <span className="font-medium">
                      {c.fee.currency === "INR" ? "₹" : `${c.fee.currency ?? ""} `}
                      {c.fee.amount}
                    </span>
                  )}
                  <span className="text-slate-600">{c.fee.reason}</span>
                </div>
                {c.fee.exempt_groups.length > 0 && (
                  <p className="mt-1 text-slate-600">Exempted by the advertisement: {c.fee.exempt_groups.map(groupLabel).join(", ")}</p>
                )}
                {c.fee.citations.map((ct, i) => (
                  <Citation key={i} c={ct} />
                ))}
              </div>
            )}
            {c.payment && (
              <div className="border-t border-slate-100 pt-2">
                <p>
                  <span className="text-slate-500">Payment: </span>
                  {c.payment.modes.length ? c.payment.modes.map((m) => m.replace(/_/g, " ").toLowerCase()).join(", ") : c.payment.text}
                </p>
                {c.payment.modes.length > 0 && <p className="text-xs text-slate-500">“{c.payment.text}”</p>}
                <Citation c={c.payment.citation} showExcerpt={false} />
              </div>
            )}
            {c.fee_rules.map((r) => (
              <RuleItem key={r.code} r={r} />
            ))}
          </div>
        </section>
      )}

      {c.photo_id && (
        <section>
          <h2 className="mb-2 font-medium">Photo ID</h2>
          <div className="rounded border border-slate-200 bg-white p-3 text-sm">
            <p>Enter the details of one of these cards in the registration profile and carry it to the exam:</p>
            <ul className="mt-1 list-disc pl-5">
              {c.photo_id.accepted.map((card) => (
                <li key={card}>{card}</li>
              ))}
            </ul>
            {c.photo_id.aadhaar_advised && <p className="mt-1 text-slate-700">The advertisement strongly advises using the Aadhaar card.</p>}
            <Citation c={c.photo_id.citation} />
          </div>
        </section>
      )}

      <section>
        <h2 className="mb-2 font-medium">Documents and certificates to upload</h2>
        {docs.length === 0 ? (
          <p className="text-sm text-slate-500">No document list was extracted from the advertisement.</p>
        ) : (
          <ul className="space-y-2">
            {docs.map((d) => (
              <DocumentItem key={`${d.code}-${d.citation.page}-${d.text?.slice(0, 20)}`} d={d} />
            ))}
          </ul>
        )}
        {(notAccepted.length > 0 || otherUploadRules.length > 0) && (
          <div className="mt-3 space-y-2">
            {notAccepted.map((r) => (
              <div key={r.code} className="rounded border border-red-200 bg-red-50 p-3 text-sm">
                <p className="font-medium text-red-900">Do not upload: {r.documents!.join(", ")}</p>
                <Citation c={r.citation} />
              </div>
            ))}
            {otherUploadRules.map((r) => (
              <div key={r.code} className="rounded border border-slate-200 bg-white p-3 text-sm">
                <RuleItem r={r} />
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function DocumentItem({ d }: { d: PrepDocument }) {
  const muted = d.status === "NOT_REQUIRED";
  return (
    <li className={`rounded border p-3 text-sm ${muted ? "border-slate-200 bg-slate-50 text-slate-600" : "border-slate-200 bg-white"}`}>
      <div className="flex flex-wrap items-center gap-2">
        <Pill value={d.status} />
        <span className="font-medium">{codeLabel(d.code)}</span>
        <span className="text-slate-600">— {d.reason}</span>
      </div>
      {d.text && <p className="mt-1 whitespace-pre-wrap">{d.text}</p>}
      <Citation c={d.citation} />
    </li>
  );
}

function RuleItem({ r }: { r: PrepRule }) {
  return (
    <div className="border-t border-slate-100 pt-2 first:border-t-0 first:pt-0">
      <p>
        <span className="text-slate-500">{codeLabel(r.code)}: </span>
        {r.text ?? r.documents?.join(", ")}
      </p>
      <Citation c={r.citation} showExcerpt={false} />
    </div>
  );
}

function codeLabel(code: string): string {
  const s = code.replace(/_/g, " ").toLowerCase();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function groupLabel(g: string): string {
  return ({ PWBD: "PwBD", FEMALE: "women" } as Record<string, string>)[g] ?? g;
}
