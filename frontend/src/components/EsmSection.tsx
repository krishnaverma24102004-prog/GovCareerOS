/**
 * The ex-servicemen / Agniveer section of an eligibility result (backend eligibility/esm.py). Shown apart from the
 * hard checks: it never changes the verdict above it. Each row quotes the notice and names its page.
 */
import { Badge } from "@/components/ui";
import type { EligibilityEvidence } from "@/lib/types";

const LABEL: Record<string, string> = {
  ESM_STATUS: "Ex-serviceman status (definition)",
  ESM_RELEASE: "Release / discharge date",
  ESM_AGE: "Ex-servicemen age relaxation",
  ESM_RESERVATION_REUSE: "ESM reservation already used",
  ESM_AGNIVEER: "Former Agniveer",
};

export function EsmSection({ rows, status, pageHref }: { rows?: EligibilityEvidence[]; status?: string | null; pageHref: (page: string) => string | null }) {
  if (!rows?.length) return null;
  return (
    <div className="mt-4 border-t border-slate-200 pt-3">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium">Ex-servicemen / Agniveer</span>
        {status && <Badge value={status} />}
        <span className="text-xs text-slate-500">separate from the verdict above; the official notification prevails</span>
      </div>
      <ul className="space-y-3">
        {rows.map((c, i) => {
          const href = c.source_page ? pageHref(c.source_page) : null;
          return (
            <li key={`${c.check_name}-${i}`} className="border-t border-slate-100 pt-2 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{LABEL[c.check_name] ?? c.check_name}</span>
                <Badge value={c.outcome} />
                {c.source_page &&
                  (href ? (
                    <a className="ml-auto text-xs text-blue-700 underline" href={href} target="_blank" rel="noreferrer">
                      page {c.source_page}
                    </a>
                  ) : (
                    <span className="ml-auto text-xs">page {c.source_page}</span>
                  ))}
              </div>
              <p>
                <span className="text-slate-500">Reason: </span>
                {c.rule}
              </p>
              {c.requirement !== "-" && (
                <p>
                  <span className="text-slate-500">Notification says: </span>
                  {c.requirement}
                </p>
              )}
              <p>
                <span className="text-slate-500">Your profile: </span>
                {c.profile_evidence}
              </p>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
