"use client";
/**
 * Truth-locked resume for the latest profile version, optionally tailored to one vacancy.
 *
 * Every resume line is a claim holding one profile value verbatim, linked to a Fact and that Fact's
 * Evidence (backend/resume/builder.py). A line is "verified" only when a reviewer verified its Fact;
 * everything else carries a visible "unverified" mark. Nothing on this page is generated or reworded.
 *
 * The backend never implies "the current profile": the page resolves the latest version id first and
 * sends it explicitly with POST /resume. Downloads go through GET /resume/{id}/download with the bearer
 * token, so they are fetched as a blob instead of a plain link.
 */
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";

import { Badge, ErrorNote, Shell, errText, fmtDate, useSession } from "@/components/ui";
import { api, ApiError, API_URL, getToken } from "@/lib/api";
import type { Profile, VacancyDetail } from "@/lib/types";
import type { Resume, ResumeClaim, ResumeEvidence, ResumeFormat, VacancyOption } from "@/lib/types-resume";

const LAST_RESUME_KEY = "govcareeros.resume.last";
const MEETS = "meets_requirement";

const SECTION_LABEL: Record<string, string> = {
  personal_info: "Personal details",
  [MEETS]: "Meets requirement",
  experience: "Experience",
  education: "Education",
  projects: "Projects",
  skills: "Skills",
  certifications: "Certifications",
  languages: "Languages",
  achievements: "Achievements",
};

const humanize = (s: string) => s.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

/** PDF viewers open at a page with the #page=N fragment. */
function pageLink(url: string | null, page: number | null): string | null {
  if (!url) return null;
  return page ? `${url}#page=${page}` : url;
}

function readLastResume(): string | null {
  try {
    return window.sessionStorage.getItem(LAST_RESUME_KEY);
  } catch {
    return null;
  }
}

function writeLastResume(id: string | null): void {
  try {
    if (id) window.sessionStorage.setItem(LAST_RESUME_KEY, id);
    else window.sessionStorage.removeItem(LAST_RESUME_KEY);
  } catch {
    // storage blocked: the last resume is simply not reopened after a reload
  }
}

/** Downloads need the bearer token, so the file is fetched and handed to the browser as a blob. */
async function downloadResume(id: string, format: ResumeFormat): Promise<void> {
  const token = getToken();
  let res: Response;
  try {
    res = await fetch(`${API_URL}/resume/${id}/download?format=${format}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
  } catch {
    throw new ApiError(0, `cannot reach the API at ${API_URL}`);
  }
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as { detail?: unknown } | null;
    const msg = typeof body?.detail === "string" ? body.detail : res.statusText || `HTTP ${res.status}`;
    throw new ApiError(res.status, `download failed: ${msg}`);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `resume-${id}.${format}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export default function ResumePage() {
  const user = useSession();
  return <Shell user={user}>{user && <ResumeView />}</Shell>;
}

function ResumeView() {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [noProfile, setNoProfile] = useState(false);
  const [profileError, setProfileError] = useState<string | null>(null);

  const [vacancyId, setVacancyId] = useState<string>("");
  const [resume, setResume] = useState<Resume | null>(null);
  const [building, setBuilding] = useState(false);
  const [buildError, setBuildError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    // Deep link: /resume?vacancy_id=<id> preselects the vacancy to tailor to
    const pre = new URLSearchParams(window.location.search).get("vacancy_id");
    if (pre) setVacancyId(pre);
    api<Profile>("/profile")
      .then((p) => live && setProfile(p))
      .catch((e) => {
        if (!live) return;
        if (e instanceof ApiError && e.status === 404) setNoProfile(true);
        else setProfileError(errText(e));
      });
    // Reopen the resume built earlier in this tab (there is no list endpoint; ids live only in this session)
    const last = readLastResume();
    if (last) {
      api<Resume>(`/resume/${last}`)
        .then((r) => live && setResume(r))
        .catch((e) => {
          if (e instanceof ApiError && e.status === 404) writeLastResume(null);
        });
    }
    return () => {
      live = false;
    };
  }, []);

  async function build(e: FormEvent) {
    e.preventDefault();
    if (!profile) return;
    setBuilding(true);
    setBuildError(null);
    try {
      const r = await api<Resume>("/resume", {
        method: "POST",
        body: { profile_version_id: profile.version.id, vacancy_id: vacancyId || null },
      });
      setResume(r);
      writeLastResume(r.id);
    } catch (err) {
      setBuildError(errText(err));
    } finally {
      setBuilding(false);
    }
  }

  if (noProfile)
    return (
      <p>
        No profile yet. <Link className="text-blue-700 underline" href="/profile">Create one</Link> to build a resume.
      </p>
    );

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-baseline gap-3">
        <h1 className="text-xl font-semibold">Resume</h1>
        {profile && <span className="text-sm text-slate-500">from profile version {profile.version.version_number}</span>}
      </div>
      <ErrorNote error={profileError} />
      {!profile && !profileError && <p className="text-slate-500">Loading profile…</p>}

      {profile && (
        <form onSubmit={build} className="space-y-3 rounded border border-slate-200 bg-white p-4">
          <p className="text-sm text-slate-600">
            Every line is one value from your profile, copied word for word and linked to the fact and evidence behind it. Nothing is
            added or reworded. Tailoring only reorders sections and adds a &ldquo;Meets requirement&rdquo; section that quotes the official
            notification.
          </p>
          <VacancyPicker value={vacancyId} onChange={setVacancyId} />
          <button disabled={building} className="rounded bg-blue-600 px-4 py-2 text-white disabled:opacity-50">
            {building ? "Building…" : vacancyId ? "Build tailored resume" : "Build resume"}
          </button>
          <ErrorNote error={buildError} />
        </form>
      )}

      {resume && <ResumeResult resume={resume} currentVersion={profile} />}
    </div>
  );
}

/** Search the stored vacancies (GET /vacancies) and pick one to tailor to, or none for a general resume. */
function VacancyPicker({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const [q, setQ] = useState("");
  const [options, setOptions] = useState<VacancyOption[] | null>(null);
  const [selected, setSelected] = useState<VacancyOption | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    const t = setTimeout(() => {
      setError(null);
      const params = new URLSearchParams({ limit: "50" });
      if (q.trim()) params.set("q", q.trim());
      api<VacancyOption[]>(`/vacancies?${params}`)
        .then((vs) => live && setOptions(vs))
        .catch((e) => live && setError(errText(e)));
    }, 300);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [q]);

  // A preselected vacancy (deep link) may not be in the current search results: fetch it to label it
  useEffect(() => {
    if (!value) {
      setSelected(null);
      return;
    }
    const inList = options?.find((o) => o.id === value);
    if (inList) {
      setSelected(inList);
      return;
    }
    if (selected?.id === value) return;
    let live = true;
    api<VacancyDetail>(`/vacancies/${value}`)
      .then((v) => live && setSelected({ ...v, recruitment_mode: "UNKNOWN" }))
      .catch((e) => live && setError(`selected vacancy: ${errText(e)}`));
    return () => {
      live = false;
    };
  }, [value, options, selected?.id]);

  const shown = useMemo(() => {
    const list = options ?? [];
    return selected && !list.some((o) => o.id === selected.id) ? [selected, ...list] : list;
  }, [options, selected]);

  const input = "w-full rounded border border-slate-300 px-2 py-1";
  return (
    <div className="grid gap-3 md:grid-cols-[16rem_1fr]">
      <label className="block text-sm">
        Search vacancies
        <input className={input} value={q} onChange={(e) => setQ(e.target.value)} placeholder="post, ministry or department" />
      </label>
      <label className="block text-sm">
        Tailor to
        <select className={input} value={value} onChange={(e) => onChange(e.target.value)}>
          <option value="">No vacancy (general resume)</option>
          {shown.map((v) => (
            <option key={v.id} value={v.id}>
              {v.post_name} — {v.organization}
              {v.application_deadline ? ` (last date ${fmtDate(v.application_deadline)})` : ""}
            </option>
          ))}
        </select>
        {options === null && !error && <span className="text-xs text-slate-500">Loading vacancies…</span>}
        {options?.length === 0 && <span className="text-xs text-slate-500">No vacancies match{q.trim() ? ` "${q.trim()}"` : ""}.</span>}
      </label>
      <div className="md:col-span-2">
        <ErrorNote error={error} />
      </div>
    </div>
  );
}

interface Section {
  name: string;
  items: { ref: string; claims: ResumeClaim[] }[];
}

/** Claims arrive ordered by position; sections and items keep that order. */
function groupClaims(claims: ResumeClaim[]): Section[] {
  const sections: Section[] = [];
  for (const c of [...claims].sort((a, b) => a.position - b.position)) {
    let s = sections.find((x) => x.name === c.section);
    if (!s) sections.push((s = { name: c.section, items: [] }));
    // meets_requirement lines are one item per requirement pairing, not per profile row
    const ref = c.section === MEETS ? `${c.requirement_id ?? ""}|${c.item_ref}` : c.item_ref;
    let item = s.items.find((i) => i.ref === ref);
    if (!item) s.items.push((item = { ref, claims: [] }));
    item.claims.push(c);
  }
  return sections;
}

function ResumeResult({ resume, currentVersion }: { resume: Resume; currentVersion: Profile | null }) {
  const [vacancy, setVacancy] = useState<VacancyDetail | null>(null);
  const [vacancyError, setVacancyError] = useState<string | null>(null);
  const [busy, setBusy] = useState<ResumeFormat | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  useEffect(() => {
    setVacancy(null);
    setVacancyError(null);
    if (!resume.vacancy_id) return;
    let live = true;
    api<VacancyDetail>(`/vacancies/${resume.vacancy_id}`)
      .then((v) => live && setVacancy(v))
      .catch((e) => live && setVacancyError(`Could not load the vacancy for citation links: ${errText(e)}`));
    return () => {
      live = false;
    };
  }, [resume.vacancy_id]);

  const download = useCallback(
    async (format: ResumeFormat) => {
      setBusy(format);
      setDownloadError(null);
      try {
        await downloadResume(resume.id, format);
      } catch (e) {
        setDownloadError(errText(e));
      } finally {
        setBusy(null);
      }
    },
    [resume.id],
  );

  const sections = useMemo(() => groupClaims(resume.claims), [resume.claims]);
  const verified = resume.claims.filter((c) => c.verified).length;
  const stale = currentVersion && currentVersion.version.id !== resume.profile_version_id;

  // Requirements are quoted from the vacancy's source document; prefer the URL that document was fetched from
  const notificationUrl = useMemo(() => {
    if (!vacancy) return null;
    const src = vacancy.provenance.find((p) => p.raw_document_id === vacancy.raw_document_id && p.document_url);
    return src?.document_url ?? vacancy.notification_url;
  }, [vacancy]);

  return (
    <section className="space-y-4">
      <div className="rounded border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="font-medium">{resume.vacancy_id ? "Tailored resume" : "General resume"}</h2>
          <span className="text-sm text-slate-500">
            built {fmtDate(resume.created_at, true)} · {resume.builder_version}
          </span>
          <div className="ml-auto flex gap-2">
            {(["html", "docx"] as const).map((f) => (
              <button
                key={f}
                onClick={() => download(f)}
                disabled={busy !== null}
                className="rounded border border-slate-300 px-3 py-1 text-sm hover:bg-slate-100 disabled:opacity-50"
              >
                {busy === f ? "Downloading…" : `Download ${f.toUpperCase()}`}
              </button>
            ))}
          </div>
        </div>
        {resume.vacancy_id && (
          <p className="mt-2 text-sm">
            For{" "}
            <Link className="text-blue-700 underline" href={`/vacancies/${resume.vacancy_id}`}>
              {vacancy ? `${vacancy.post_name} — ${vacancy.organization}` : "the selected vacancy"}
            </Link>
            {notificationUrl && (
              <>
                {" · "}
                <a className="text-blue-700 underline" href={notificationUrl} target="_blank" rel="noreferrer">
                  official notification
                </a>
              </>
            )}
          </p>
        )}
        <p className="mt-2 text-sm text-slate-600">
          {verified} of {resume.claims.length} lines verified by a reviewer.{" "}
          {resume.claims.length > verified && "Unverified lines are printed with an “unverified” mark in the downloads too."}
        </p>
        {stale && (
          <p className="mt-2 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
            This resume was built from an earlier profile version. Build again to use version {currentVersion?.version.version_number}.
          </p>
        )}
        <ErrorNote error={downloadError} />
        <ErrorNote error={vacancyError} />
      </div>

      <Legend />

      {resume.claims.length === 0 && (
        <p className="rounded border border-slate-200 bg-white p-4 text-sm text-slate-600">
          This profile version has nothing to put on a resume yet (or every value was rejected in review). Add education,
          experience or skills to your <Link className="text-blue-700 underline" href="/profile">profile</Link> and build again.
        </p>
      )}

      {resume.vacancy_id && !sections.some((s) => s.name === MEETS) && resume.claims.length > 0 && (
        <div className="rounded border border-slate-200 bg-white p-4">
          <h3 className="mb-1 font-medium">{SECTION_LABEL[MEETS]}</h3>
          <p className="text-sm text-slate-600">
            No education or experience requirement of this vacancy was judged met (or conditionally met) by an entry in your profile.
            See the eligibility checks on the{" "}
            <Link className="text-blue-700 underline" href={`/vacancies/${resume.vacancy_id}`}>
              vacancy page
            </Link>
            .
          </p>
        </div>
      )}

      {sections.map((s) =>
        s.name === MEETS ? (
          <MeetsSection key={s.name} section={s} documentUrl={notificationUrl} vacancyLoaded={!!vacancy} />
        ) : (
          <ClaimSection key={s.name} section={s} />
        ),
      )}
    </section>
  );
}

function Legend() {
  return (
    <div className="flex flex-wrap items-center gap-4 text-xs text-slate-600">
      <span className="flex items-center gap-1">
        <VerifiedMark verified /> fact verified by a reviewer
      </span>
      <span className="flex items-center gap-1">
        <VerifiedMark verified={false} /> your own statement, not yet verified
      </span>
      <span>Hover a line for its source; expand it for the full fact → evidence chain.</span>
    </div>
  );
}

function VerifiedMark({ verified }: { verified: boolean }) {
  return verified ? (
    <span className="inline-block rounded bg-green-100 px-1.5 py-0.5 text-xs font-medium text-green-800" aria-label="verified">
      ✓ verified
    </span>
  ) : (
    <span className="inline-block rounded bg-amber-100 px-1.5 py-0.5 text-xs font-medium text-amber-800" aria-label="unverified">
      ! unverified
    </span>
  );
}

function hoverSummary(c: ResumeClaim): string {
  const ev = c.fact.evidence.map((e) => `${e.evidence_type}${e.source ? `: ${e.source}` : ""}${e.artifact_url ? ` (${e.artifact_url})` : ""}`);
  return [
    `Fact ${c.fact.subject}.${c.fact.predicate} = "${c.fact.value}" [${c.fact.status}]`,
    ...(ev.length ? ev.map((e) => `← ${e}`) : ["← no evidence attached"]),
  ].join("\n");
}

function ClaimSection({ section }: { section: Section }) {
  return (
    <div className="rounded border border-slate-200 bg-white p-4">
      <h3 className="mb-3 font-medium">{SECTION_LABEL[section.name] ?? humanize(section.name)}</h3>
      <ul className="space-y-3">
        {section.items.map((item) => (
          <li key={item.ref} className="border-t border-slate-100 pt-2 first:border-t-0 first:pt-0">
            {item.claims.map((c, i) => (
              <ClaimLine key={c.id} claim={c} heading={i === 0 && section.name !== "personal_info"} />
            ))}
          </li>
        ))}
      </ul>
    </div>
  );
}

function ClaimLine({ claim, heading }: { claim: ResumeClaim; heading: boolean }) {
  return (
    <details className="group text-sm">
      <summary className="flex cursor-pointer list-none flex-wrap items-baseline gap-2 rounded px-1 py-0.5 hover:bg-slate-50" title={hoverSummary(claim)}>
        <span className="w-32 shrink-0 text-slate-500">{humanize(claim.field)}</span>
        <span className={`min-w-0 flex-1 whitespace-pre-wrap ${heading ? "font-medium" : ""}`}>{claim.text}</span>
        <VerifiedMark verified={claim.verified} />
        <span className="text-xs text-slate-400 group-open:hidden">show source ▸</span>
        <span className="hidden text-xs text-slate-400 group-open:inline">hide ▾</span>
      </summary>
      <EvidenceChain claim={claim} />
    </details>
  );
}

function EvidenceChain({ claim }: { claim: ResumeClaim }) {
  const f = claim.fact;
  return (
    <div className="mb-2 ml-1 mt-1 border-l-2 border-slate-200 pl-3 text-xs">
      <p className="text-slate-500">Resume line</p>
      <p className="mb-1">&ldquo;{claim.text}&rdquo;</p>
      <p className="text-slate-500">↓ backed by fact</p>
      <p className="mb-1 flex flex-wrap items-center gap-2">
        <span className="font-mono">
          {f.subject}.{f.predicate}
        </span>
        = &ldquo;{f.value}&rdquo; <Badge value={f.status} />
      </p>
      <p className="text-slate-500">↓ evidence</p>
      {f.evidence.length === 0 && <p className="text-slate-500">No evidence attached.</p>}
      <ul className="space-y-1">
        {f.evidence.map((e) => (
          <EvidenceRow key={e.id} e={e} />
        ))}
      </ul>
      {!claim.verified && (
        <p className="mt-1 text-amber-800">Unverified: only a reviewer&rsquo;s decision on the fact verifies it; attached evidence alone does not.</p>
      )}
    </div>
  );
}

function EvidenceRow({ e }: { e: ResumeEvidence }) {
  return (
    <li className="flex flex-wrap items-center gap-2">
      <Badge value={e.evidence_type} />
      {e.source && <span className="text-slate-700">{e.source}</span>}
      {e.artifact_url ? (
        <a className="break-all text-blue-700 underline" href={e.artifact_url} target="_blank" rel="noreferrer">
          stored document
        </a>
      ) : (
        e.artifact_id && <span className="text-slate-500">stored document {e.artifact_id.slice(0, 8)} (no source URL)</span>
      )}
      {e.artifact_content_hash && (
        <span className="font-mono text-slate-400" title={e.artifact_content_hash}>
          sha {e.artifact_content_hash.slice(0, 12)}
        </span>
      )}
    </li>
  );
}

function MeetsSection({ section, documentUrl, vacancyLoaded }: { section: Section; documentUrl: string | null; vacancyLoaded: boolean }) {
  return (
    <div className="rounded border border-slate-200 bg-white p-4">
      <h3 className="mb-1 font-medium">{SECTION_LABEL[MEETS]}</h3>
      <p className="mb-3 text-xs text-slate-500">
        Each requirement is quoted from the official notification next to the profile entry the eligibility engine matched it with.
      </p>
      <ul className="space-y-4">
        {section.items.map((item) => {
          const first = item.claims[0];
          const href = pageLink(documentUrl, first.requirement_page);
          return (
            <li key={item.ref} className="border-t border-slate-100 pt-3 first:border-t-0 first:pt-0">
              <div className="mb-1 flex flex-wrap items-center gap-2 text-sm">
                <span className="text-slate-500">Notification requires</span>
                {first.requirement_outcome && <Badge value={first.requirement_outcome} />}
                <span className="ml-auto text-xs">
                  {href ? (
                    <a className="text-blue-700 underline" href={href} target="_blank" rel="noreferrer">
                      official notification{first.requirement_page ? `, page ${first.requirement_page}` : " (page not recorded)"}
                    </a>
                  ) : (
                    <span className="text-slate-500">
                      {first.requirement_page ? `page ${first.requirement_page}` : "page not recorded"}
                      {vacancyLoaded ? " · no document URL stored" : ""}
                    </span>
                  )}
                </span>
              </div>
              <blockquote className="mb-2 whitespace-pre-wrap border-l-4 border-blue-200 bg-blue-50 px-3 py-2 text-sm italic text-slate-800">
                &ldquo;{first.requirement_quote ?? "—"}&rdquo;
              </blockquote>
              <p className="mb-1 text-xs text-slate-500">Your profile</p>
              {item.claims.map((c) => (
                <ClaimLine key={c.id} claim={c} heading />
              ))}
              {first.requirement_outcome === "CONDITIONAL" && (
                <p className="mt-1 text-xs text-amber-800">Conditionally met: see the eligibility check on the vacancy page for the condition.</p>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
