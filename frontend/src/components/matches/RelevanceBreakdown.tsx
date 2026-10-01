"use client";
/**
 * The stored relevance breakdown for one match (GET /relevance/{id}): every component with its score,
 * effective weight, contribution and evidence; the components that were not scored with the reason;
 * and, per component, the official notification passages it reads, each linked to the document page
 * (from GET /vacancies/{id}).
 */
import { useEffect, useState } from "react";

import { ErrorNote, errText, fmtDate } from "@/components/ui";
import { api } from "@/lib/api";
import type { VacancyDetail } from "@/lib/types";
import type { OfficialCitation, RelevanceComponent, RelevanceDetail } from "@/lib/types-matches-relevance";

import { citationsFor, pageLink } from "./citations";

const LABELS: Record<string, string> = {
  education: "Education",
  skills: "Skills",
  experience: "Experience",
  domain: "Domain",
  location: "Location",
  role: "Role",
};

export function RelevanceBreakdown({ relevanceId, vacancyId }: { relevanceId: string; vacancyId: string }) {
  const [rel, setRel] = useState<RelevanceDetail | null>(null);
  const [relError, setRelError] = useState<string | null>(null);
  const [vacancy, setVacancy] = useState<VacancyDetail | null>(null);
  const [vacError, setVacError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setRel(null);
    setRelError(null);
    setVacancy(null);
    setVacError(null);
    api<RelevanceDetail>(`/relevance/${relevanceId}`)
      .then((r) => live && setRel(r))
      .catch((e) => live && setRelError(errText(e)));
    api<VacancyDetail>(`/vacancies/${vacancyId}`)
      .then((v) => live && setVacancy(v))
      .catch((e) => live && setVacError(errText(e)));
    return () => {
      live = false;
    };
  }, [relevanceId, vacancyId]);

  if (relError) return <ErrorNote error={`Could not load the relevance breakdown: ${relError}`} />;
  if (!rel) return <p className="text-sm text-slate-500">Loading relevance breakdown…</p>;

  const scored = rel.components.filter((c) => c.score !== null);
  const unscored = rel.components.filter((c) => c.score === null);

  return (
    <div className="space-y-4 text-sm">
      <p className="text-slate-600">
        Relevance <span className="font-semibold tabular-nums text-slate-900">{rel.score.toFixed(1)}</span> / 100 = sum of
        score × effective weight over the {scored.length} scored component{scored.length === 1 ? "" : "s"}.
        <span className="text-slate-500">
          {" "}
          Engine {rel.engine_version} · computed {fmtDate(rel.created_at, true)}
        </span>
      </p>

      {rel.components.length === 0 ? (
        <p className="text-slate-500">The stored score has no component breakdown.</p>
      ) : (
        <div className="overflow-x-auto rounded border border-slate-200">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-slate-600">
              <tr>
                <th className="px-3 py-2">Component</th>
                <th className="px-3 py-2 text-right">Score</th>
                <th className="px-3 py-2 text-right" title="Configured weight → effective weight after redistributing unscored components">
                  Weight
                </th>
                <th className="px-3 py-2 text-right">Points</th>
                <th className="px-3 py-2">Evidence</th>
              </tr>
            </thead>
            <tbody>
              {rel.components.map((c) => (
                <ComponentRow key={c.name} c={c} configured={rel.weights[c.name]} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {unscored.length > 0 && (
        <div className="rounded border border-sky-200 bg-sky-50 px-3 py-2">
          <p className="font-medium text-sky-900">Not scored ({unscored.length})</p>
          <p className="mb-1 text-xs text-sky-800">
            A missing input never counts as zero: these components were left out and their weight was shared among the scored ones.
          </p>
          <ul className="list-disc pl-5 text-sky-900">
            {unscored.map((c) => (
              <li key={c.name}>
                <span className="font-medium">{LABELS[c.name] ?? c.name}</span>: {c.evidence.join("; ") || "no reason recorded"}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <p className="mb-1 font-medium">Official sources each component reads</p>
        {vacError && <ErrorNote error={`Could not load the vacancy's citations: ${vacError}`} />}
        {!vacancy && !vacError && <p className="text-slate-500">Loading citations…</p>}
        {vacancy && <CitationList components={rel.components} vacancy={vacancy} />}
      </div>
    </div>
  );
}

function ComponentRow({ c, configured }: { c: RelevanceComponent; configured: number | undefined }) {
  const notScored = c.score === null;
  return (
    <tr className={`border-t border-slate-100 align-top ${notScored ? "bg-slate-50 text-slate-500" : ""}`}>
      <td className="px-3 py-2 font-medium">{LABELS[c.name] ?? c.name}</td>
      <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums">
        {notScored ? <span className="rounded bg-sky-100 px-1.5 py-0.5 text-xs text-sky-800">not scored</span> : c.score!.toFixed(0)}
      </td>
      <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums">
        {configured !== undefined && <span className="text-slate-400">{configured} → </span>}
        {c.weight.toFixed(1)}%
      </td>
      <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums">{notScored ? "—" : ((c.score! * c.weight) / 100).toFixed(1)}</td>
      <td className="px-3 py-2">
        {c.evidence.length ? (
          <ul className="space-y-0.5">
            {c.evidence.map((e, i) => (
              <li key={i} className="break-words">
                {e}
              </li>
            ))}
          </ul>
        ) : (
          <span className="text-slate-400">no evidence recorded</span>
        )}
      </td>
    </tr>
  );
}

function CitationList({ components, vacancy }: { components: RelevanceComponent[]; vacancy: VacancyDetail }) {
  const groups = components.map((c) => ({ c, cites: citationsFor(c.name, vacancy) }));
  if (groups.every((g) => g.cites.length === 0))
    return <p className="text-slate-500">No requirement or field provenance is stored for this vacancy.</p>;
  return (
    <div className="space-y-2">
      {groups.map(({ c, cites }) => (
        <div key={c.name}>
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            {LABELS[c.name] ?? c.name}
            {c.score === null && <span className="ml-1 font-normal normal-case">(not scored)</span>}
          </p>
          {cites.length === 0 ? (
            <p className="text-slate-400">no notification passage stored for this component</p>
          ) : (
            <ul className="space-y-1">
              {cites.map((cite) => (
                <CitationItem key={cite.key} cite={cite} />
              ))}
            </ul>
          )}
        </div>
      ))}
    </div>
  );
}

function CitationItem({ cite }: { cite: OfficialCitation }) {
  const href = pageLink(cite.url, cite.page);
  const where = cite.page ? `page ${cite.page}` : "document (page not recorded)";
  return (
    <li className="flex flex-wrap items-baseline gap-x-2">
      <span className="font-mono text-xs text-slate-500">{cite.label}</span>
      <span className="min-w-0 flex-1 break-words text-slate-700">{cite.text ? `“${cite.text}”` : "—"}</span>
      <span className="whitespace-nowrap text-xs">
        {href ? (
          <a className="text-blue-700 underline" href={href} target="_blank" rel="noreferrer">
            {where}
          </a>
        ) : (
          <span className="text-slate-500">{cite.page ? `page ${cite.page}` : "no page"} · no document link stored</span>
        )}
        {cite.section && <span className="text-slate-500"> · {cite.section}</span>}
      </span>
    </li>
  );
}
