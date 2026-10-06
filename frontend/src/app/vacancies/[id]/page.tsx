"use client";
/**
 * One vacancy with its evidence trail: every stored field with the page and quote it came from and a
 * link to the official document, the active requirements, and the eligibility checks for the user's
 * latest profile version with the reason for each outcome.
 *
 * Eligibility: the newest stored result for (latest version, vacancy) is shown; if there is none yet
 * the deterministic engine is run once via POST /eligibility.
 *
 * Also: the official closing date with its evidence (GET /vacancies/{id}/deadline), the field-level change log
 * from corrigenda (GET /vacancies/{id}/changes) and a link to the printable apply-ready checklist.
 */
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { ChangeLog, DeadlinePanel } from "@/components/changes";
import { Badge, ErrorNote, Shell, errText, fmtDate, useSession } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import type { Eligibility, Profile, Provenance, VacancyDetail } from "@/lib/types";

export default function VacancyPage() {
  const user = useSession();
  const { id } = useParams<{ id: string }>();
  return <Shell user={user}>{user && id && <VacancyView id={id} />}</Shell>;
}

/** PDF viewers open at a page with the #page=N fragment. */
function pageLink(url: string | null, page: number | string | null): string | null {
  if (!url) return null;
  return page ? `${url}#page=${page}` : url;
}

function VacancyView({ id }: { id: string }) {
  const [v, setV] = useState<VacancyDetail | null>(null);
  const [elig, setElig] = useState<Eligibility | null>(null);
  const [versionNumber, setVersionNumber] = useState<number | null>(null);
  const [eligNote, setEligNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<VacancyDetail>(`/vacancies/${id}`).then(setV).catch((e) => setError(errText(e)));
    (async () => {
      let profile: Profile;
      try {
        profile = await api<Profile>("/profile");
      } catch (e) {
        setEligNote(e instanceof ApiError && e.status === 404 ? "Create a profile to see eligibility." : errText(e));
        return;
      }
      setVersionNumber(profile.version.version_number);
      try {
        const q = `vacancy_id=${id}&profile_version_id=${profile.version.id}&limit=1`;
        const [stored] = await api<Eligibility[]>(`/eligibility?${q}`);
        setElig(
          stored ??
            (await api<Eligibility>("/eligibility", {
              method: "POST",
              body: { profile_version_id: profile.version.id, vacancy_id: id },
            })),
        );
      } catch (e) {
        setEligNote(errText(e));
      }
    })();
  }, [id]);

  if (error) return <ErrorNote error={error} />;
  if (!v) return <p className="text-slate-500">Loading…</p>;

  const facts: [string, string | number | null][] = [
    ["Organization", v.organization],
    ["Ministry", v.ministry],
    ["Department", v.department],
    ["Vacancies", v.vacancy_count],
    ["Advertisement no.", v.advertisement_number],
    ["Pay", v.pay_scale],
    ["Headquarters", v.headquarters],
    ["Application mode", v.application_mode],
    ["Opens", v.application_start ? fmtDate(v.application_start, true) : null],
    ["Last date", v.application_deadline ? fmtDate(v.application_deadline, true) : null],
    ["Eligibility crucial date", v.eligibility_crucial_date ? fmtDate(v.eligibility_crucial_date) : null],
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">{v.post_name}</h1>
        <p className="text-sm text-slate-500">
          {v.external_ref && <>Ref {v.external_ref} · </>}parser confidence {(v.confidence * 100).toFixed(0)}%
        </p>
        <div className="mt-2 flex flex-wrap gap-3 text-sm">
          {v.notification_url && (
            <a className="text-blue-700 underline" href={v.notification_url} target="_blank" rel="noreferrer">
              Official notification
            </a>
          )}
          {v.application_url && (
            <a className="text-blue-700 underline" href={v.application_url} target="_blank" rel="noreferrer">
              Apply (official site)
            </a>
          )}
          <Link className="text-blue-700 underline" href={`/apply-ready/vacancy/${v.id}`}>
            Apply-ready checklist
          </Link>
        </div>
      </div>

      <section className="rounded border border-slate-200 bg-white p-4">
        <dl className="grid grid-cols-[11rem_1fr] gap-x-3 gap-y-1 text-sm">
          {facts
            .filter(([, val]) => val !== null && val !== "")
            .map(([k, val]) => (
              <div key={k} className="contents">
                <dt className="text-slate-500">{k}</dt>
                <dd>{val}</dd>
              </div>
            ))}
        </dl>
      </section>

      <section>
        <h2 className="mb-2 font-medium">Closing date</h2>
        <DeadlinePanel base={`/vacancies/${v.id}`} />
      </section>

      <section>
        <h2 className="mb-2 font-medium">Official changes</h2>
        <ChangeLog base={`/vacancies/${v.id}`} />
      </section>

      <section>
        <h2 className="mb-2 font-medium">Where each field comes from</h2>
        <ProvenanceTable rows={v.provenance} fallbackUrl={v.notification_url} />
      </section>

      <section>
        <h2 className="mb-2 font-medium">Requirements</h2>
        <ul className="space-y-2">
          {v.requirements.map((r) => (
            <li key={r.id} className="rounded border border-slate-200 bg-white p-3 text-sm">
              <div className="mb-1 flex items-center gap-2">
                <Badge value={r.req_type} />
                {!r.is_mandatory && <span className="text-xs text-slate-500">desirable</span>}
                {r.source_page && (
                  <a
                    className="ml-auto text-xs text-blue-700 underline"
                    href={pageLink(v.notification_url, r.source_page) ?? undefined}
                    target="_blank"
                    rel="noreferrer"
                  >
                    page {r.source_page}
                    {r.source_section ? ` · ${r.source_section}` : ""}
                  </a>
                )}
              </div>
              <p className="whitespace-pre-wrap">{r.value}</p>
            </li>
          ))}
          {!v.requirements.length && <li className="text-sm text-slate-500">No requirements extracted.</li>}
        </ul>
      </section>

      <section>
        <h2 className="mb-2 font-medium">
          Your eligibility {versionNumber !== null && <span className="text-sm font-normal text-slate-500">(profile version {versionNumber})</span>}
        </h2>
        {eligNote && <p className="text-sm text-slate-600">{eligNote}</p>}
        {elig && <EligibilityPanel e={elig} pdf={v.notification_url} />}
      </section>
    </div>
  );
}

function ProvenanceTable({ rows, fallbackUrl }: { rows: Provenance[]; fallbackUrl: string | null }) {
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
          {rows.map((p) => {
            const href = pageLink(p.document_url ?? fallbackUrl, p.page);
            return (
              <tr key={`${p.field_name}-${p.raw_document_id}`} className="border-t border-slate-100 align-top">
                <td className="px-3 py-2 font-mono text-xs">{p.field_name}</td>
                <td className="px-3 py-2">{p.value ?? "—"}</td>
                <td className="px-3 py-2 italic text-slate-700">{p.excerpt ? `“${p.excerpt}”` : "—"}</td>
                <td className="whitespace-nowrap px-3 py-2">
                  {href ? (
                    <a className="text-blue-700 underline" href={href} target="_blank" rel="noreferrer">
                      {p.page ? `page ${p.page}` : "document"}
                    </a>
                  ) : p.page ? (
                    `page ${p.page}`
                  ) : (
                    "—"
                  )}
                  {p.section && <div className="text-xs text-slate-500">{p.section}</div>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function EligibilityPanel({ e, pdf }: { e: Eligibility; pdf: string | null }) {
  return (
    <div className="rounded border border-slate-200 bg-white p-4">
      <div className="mb-3 flex flex-wrap items-center gap-3 text-sm">
        <Badge value={e.status} />
        <span className="text-slate-500">
          engine {e.engine_version} · evaluated {fmtDate(e.created_at, true)}
          {e.crucial_date && ` · crucial date ${fmtDate(e.crucial_date)}`}
        </span>
      </div>
      <ul className="space-y-3">
        {e.evidence.map((c, i) => (
          <li key={`${c.check_name}-${i}`} className="border-t border-slate-100 pt-2 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{c.check_name}</span>
              <Badge value={c.outcome} />
              {c.match_type && <span className="font-mono text-xs text-slate-500">{c.match_type}</span>}
              {c.source_page && (
                <a className="ml-auto text-xs text-blue-700 underline" href={pageLink(pdf, c.source_page) ?? undefined} target="_blank" rel="noreferrer">
                  page {c.source_page}
                </a>
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
