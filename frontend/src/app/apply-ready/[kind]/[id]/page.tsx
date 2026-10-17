"use client";
/**
 * Apply-ready checklist for one vacancy or exam: dates, eligibility, certificates, fee, uploads, photo ID,
 * documents and the application portal, each item with its page link and the notice's own words.
 *
 * URL: /apply-ready/{vacancy|exam}/{id}[?profile_version_id=<uuid>]
 *   GET /apply-ready/vacancies/{id} | /apply-ready/exams/{id}. Public: anyone can open it without logging in.
 *   Signed in, the profile version picker adds ?profile_version_id, and the same call (with the bearer token)
 *   returns each item with the candidate's status from the application_prep checklist / eligibility engine.
 * Print: the browser's print of this page (navigation hidden), or the backend's self-contained printable page
 * GET .../{id}.html (Hinglish labels, quotes in English); the personalised one is fetched with the token.
 */
import Link from "next/link";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { pageLink, safeHref } from "@/components/applications";
import { Badge, ErrorNote, Shell, errText, fmtDate, useOptionalSession } from "@/components/ui";
import { API_URL, api, getToken } from "@/lib/api";
import type { User, VersionSummary } from "@/lib/types";
import { APPLY_READY_PATH, type ApplyReady, type ApplyReadyItem, type ApplyReadyKind } from "@/lib/types-apply-ready";

export default function ApplyReadyPage() {
  const { user, checked } = useOptionalSession();
  return (
    <Shell user={user} publicPage>
      {checked ? (
        <Suspense fallback={<p className="text-slate-500">Loading…</p>}>
          <ApplyReadyRoute user={user} />
        </Suspense>
      ) : (
        <p className="text-slate-500">Loading…</p>
      )}
    </Shell>
  );
}

function isKind(k: string | undefined): k is ApplyReadyKind {
  return k === "vacancy" || k === "exam";
}

function ApplyReadyRoute({ user }: { user: User | null }) {
  const { kind, id } = useParams<{ kind: string; id: string }>();
  const params = useSearchParams();
  // A profile version only means something to a signed-in owner; anonymous visitors always get the public view
  const versionId = user ? params.get("profile_version_id") : null;
  if (!isKind(kind) || !id) return <ErrorNote error="Unknown checklist: the address must be /apply-ready/vacancy/<id> or /apply-ready/exam/<id>." />;
  return <ApplyReadyView key={`${kind}:${id}:${versionId ?? ""}`} kind={kind} id={id} user={user} versionId={versionId} />;
}

function ApplyReadyView({ kind, id, user, versionId }: { kind: ApplyReadyKind; id: string; user: User | null; versionId: string | null }) {
  const [data, setData] = useState<ApplyReady | null>(null);
  const [error, setError] = useState<string | null>(null);
  const path = `/apply-ready/${APPLY_READY_PATH[kind]}/${id}`;
  const query = versionId ? `?profile_version_id=${encodeURIComponent(versionId)}` : "";

  useEffect(() => {
    let live = true;
    // The public view is requested without the token so an expired session never blocks it
    api<ApplyReady>(`${path}${query}`, { auth: !!versionId })
      .then((d) => live && setData(d))
      .catch((e) => live && setError(errText(e)));
    return () => {
      live = false;
    };
  }, [path, query, versionId]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3 print:hidden">
        {user && <VersionPicker versionId={versionId} />}
        <div className="ml-auto flex flex-wrap items-center gap-3 text-sm">
          <button onClick={() => window.print()} className="rounded bg-slate-800 px-3 py-1.5 font-medium text-white hover:bg-slate-900">
            Print
          </button>
          <PrintablePageLink path={`${path}.html${query}`} personalised={!!versionId} />
        </div>
      </div>
      {!user && (
        <p className="text-sm text-slate-600 print:hidden">
          <Link className="text-blue-700 underline" href="/login">
            Log in
          </Link>{" "}
          to see your own status against each item.
        </p>
      )}
      <ErrorNote error={error} />
      {!data && !error && <p className="text-slate-500">Loading…</p>}
      {data && <Checklist d={data} personalised={!!versionId} />}
    </div>
  );
}

/** Profile versions of the signed-in user; choosing one (or the public view) rewrites ?profile_version_id. */
function VersionPicker({ versionId }: { versionId: string | null }) {
  const router = useRouter();
  const pathname = usePathname();
  const [versions, setVersions] = useState<VersionSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<VersionSummary[]>("/profile/versions")
      .then((vs) => setVersions([...vs].sort((a, b) => b.version_number - a.version_number)))
      .catch((e) => setError(errText(e)));
  }, []);

  if (error) return <span className="text-sm text-slate-500">Profile versions unavailable: {error}</span>;
  if (!versions) return <span className="text-sm text-slate-500">Loading profile versions…</span>;
  if (!versions.length)
    return (
      <span className="text-sm text-slate-600">
        <Link className="text-blue-700 underline" href="/profile">
          Create a profile
        </Link>{" "}
        to see your own status.
      </span>
    );
  return (
    <label className="flex items-center gap-2 text-sm">
      <span className="text-slate-500">Show my status for</span>
      <select
        value={versionId ?? ""}
        onChange={(e) => router.replace(e.target.value ? `${pathname}?profile_version_id=${e.target.value}` : pathname)}
        className="rounded border border-slate-300 px-2 py-1"
      >
        <option value="">Public view (no profile)</option>
        {versions.map((v) => (
          <option key={v.id} value={v.id}>
            Profile version {v.version_number} ({fmtDate(v.created_at)})
          </option>
        ))}
      </select>
    </label>
  );
}

/**
 * The backend's printable page. Public: a plain link. Personalised: it needs the bearer token, so the HTML is
 * fetched and opened from a blob URL in a new tab.
 */
function PrintablePageLink({ path, personalised }: { path: string; personalised: boolean }) {
  const [error, setError] = useState<string | null>(null);
  if (!personalised)
    return (
      <a className="text-blue-700 underline" href={`${API_URL}${path}`} target="_blank" rel="noreferrer">
        Printable page
      </a>
    );
  async function open() {
    setError(null);
    const tab = window.open("", "_blank"); // opened on the click itself so it is not blocked as a pop-up
    try {
      const token = getToken();
      const res = await fetch(`${API_URL}${path}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      // A blob page runs in this app's origin and loses the response's CSP header, so the same policy is
      // restated as a meta tag: the notice text it carries can never run script here
      const csp = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">`;
      const html = (await res.text()).replace(/<head>/i, `<head>${csp}`);
      if (!html.includes(csp)) throw new Error("unexpected printable page");
      const url = URL.createObjectURL(new Blob([html], { type: "text/html" }));
      if (tab) tab.location.href = url;
      else window.location.href = url;
    } catch (e) {
      tab?.close();
      setError(`Printable page unavailable: ${errText(e)}`);
    }
  }
  return (
    <span>
      <button className="text-blue-700 underline" onClick={open}>
        Printable page
      </button>
      {error && <span className="ml-2 text-red-700">{error}</span>}
    </span>
  );
}

function Checklist({ d, personalised }: { d: ApplyReady; personalised: boolean }) {
  const doc = safeHref(d.document_url);
  const a = d.attribution;
  return (
    <div className="space-y-6">
      <div>
        <h1 className="flex flex-wrap items-center gap-3 text-xl font-semibold">
          {d.title} <Badge value={d.status} />
        </h1>
        <p className="text-sm text-slate-500">
          {[d.organization, d.advertisement_number && `Advt ${d.advertisement_number}`, d.kind === "exam" ? "examination" : "vacancy"]
            .filter(Boolean)
            .join(" · ")}
        </p>
        <p className="mt-1 text-sm">
          <span className="text-slate-500">Last date: </span>
          {d.deadline ? <span className="font-medium">{fmtDate(d.deadline, true)}</span> : "not stated in the stored notice"}
        </p>
        <div className="mt-2 flex flex-wrap gap-3 text-sm print:hidden">
          {doc && (
            <a className="text-blue-700 underline" href={doc} target="_blank" rel="noreferrer">
              Official notification
            </a>
          )}
          <Link className="text-blue-700 underline" href={d.kind === "exam" ? `/exams/${d.id}` : `/vacancies/${d.id}`}>
            Full details
          </Link>
        </div>
        {personalised && (
          <p className="mt-2 text-xs text-slate-500">
            Your status per item comes from the deterministic checklist and eligibility engine for the chosen profile version.
          </p>
        )}
      </div>

      {d.warnings.length > 0 && (
        <ul className="list-disc space-y-1 rounded border border-amber-200 bg-amber-50 py-2 pl-8 pr-3 text-sm text-amber-900">
          {d.warnings.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      )}

      {d.sections.map((s) => (
        <section key={s.key} className="break-inside-avoid">
          <h2 className="mb-2 font-medium">{s.title}</h2>
          {s.items.length ? (
            <ul className="space-y-2">
              {s.items.map((it, i) => (
                <Item key={`${s.key}-${i}`} it={it} fallbackUrl={d.document_url} />
              ))}
            </ul>
          ) : (
            <p className="text-sm text-slate-500">The stored notice states nothing for this section.</p>
          )}
        </section>
      ))}

      <footer className="space-y-1 border-t border-slate-200 pt-3 text-xs text-slate-500">
        <p>{d.disclaimer}</p>
        {a && (
          <p>
            Source: {a.source_name} ({a.organization}
            {a.jurisdiction ? `, ${a.jurisdiction}` : ""})
            {safeHref(a.official_page_url) && (
              <>
                {" · "}
                <a className="underline" href={safeHref(a.official_page_url)!} target="_blank" rel="noreferrer">
                  official page
                </a>
              </>
            )}
            {safeHref(a.document_url) && (
              <>
                {" · "}
                <a className="underline" href={pageLink(a.document_url, a.page ?? null)!} target="_blank" rel="noreferrer">
                  document{a.page ? ` p.${a.page}` : ""}
                </a>
              </>
            )}
            {a.document_retrieved_at && ` · retrieved ${fmtDate(a.document_retrieved_at, true)}`}
          </p>
        )}
      </footer>
    </div>
  );
}

function Item({ it, fallbackUrl }: { it: ApplyReadyItem; fallbackUrl: string | null }) {
  const c = it.citation;
  const href = c ? pageLink(c.document_url ?? fallbackUrl, c.page) : null;
  return (
    <li className="break-inside-avoid rounded border border-slate-200 bg-white p-3 text-sm">
      <div className="flex flex-wrap items-start gap-2">
        <span className="font-medium">{it.label}</span>
        {it.status && <Badge value={it.status} />}
        <span className="ml-auto text-xs">
          {c && (c.page || href) ? (
            href ? (
              <a className="text-blue-700 underline" href={href} target="_blank" rel="noreferrer">
                {c.page ? `page ${c.page}` : "document"}
              </a>
            ) : (
              `page ${c.page}`
            )
          ) : (
            <span className="text-slate-400">no page cited</span>
          )}
        </span>
      </div>
      {it.detail && <p className="mt-1 whitespace-pre-wrap">{it.detail}</p>}
      {c?.quote && <p className="mt-1 text-xs italic text-slate-700">“{c.quote}”</p>}
    </li>
  );
}
