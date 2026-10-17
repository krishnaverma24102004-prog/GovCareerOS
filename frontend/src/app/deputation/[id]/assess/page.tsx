"use client";
/**
 * A serving officer's assessment against one deputation circular (POST /deputation/circulars/{id}/assess) for an
 * explicit profile version, all posts or one. Deterministic: each post's checks (age, service, government service,
 * analogous post, feeder bar, cooling-off) with its outcome (e.g. MET, NOT MET, NEEDS HUMAN REVIEW) and the reason, and the
 * forwarding checklist the circular itself asks for, each with its page and words. Nothing is stored.
 */
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { TermCite, termValue } from "@/components/deputation";
import { Badge, ErrorNote, Shell, errText, fmtDate, useSession } from "@/components/ui";
import { api } from "@/lib/api";
import type { VersionSummary } from "@/lib/types";
import type { AssessIn, CircularDetail, DeputationAssessment, PostAssessment } from "@/lib/types-deputation";

export default function DeputationAssessPage() {
  const user = useSession();
  const { id } = useParams<{ id: string }>();
  return <Shell user={user}>{user && id && <AssessView id={id} />}</Shell>;
}

function AssessView({ id }: { id: string }) {
  const [circular, setCircular] = useState<CircularDetail | null>(null);
  const [versions, setVersions] = useState<VersionSummary[] | null>(null);
  const [versionId, setVersionId] = useState("");
  const [postIndex, setPostIndex] = useState("");
  const [result, setResult] = useState<DeputationAssessment | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<CircularDetail>(`/deputation/circulars/${id}`)
      .then(setCircular)
      .catch((e) => setError(errText(e)));
    api<VersionSummary[]>("/profile/versions")
      .then((vs) => {
        const sorted = [...vs].sort((a, b) => b.version_number - a.version_number);
        setVersions(sorted);
        if (sorted[0]) setVersionId(sorted[0].id);
      })
      .catch((e) => setError(errText(e)));
  }, [id]);

  async function assess(e: FormEvent) {
    e.preventDefault();
    if (!versionId) return;
    setRunning(true);
    setError(null);
    try {
      const body: AssessIn = { profile_version_id: versionId, post_index: postIndex === "" ? null : Number(postIndex) };
      setResult(await api<DeputationAssessment>(`/deputation/circulars/${id}/assess`, { method: "POST", body }));
    } catch (err) {
      setError(errText(err));
    } finally {
      setRunning(false);
    }
  }

  const doc = result?.attribution?.document_url ?? circular?.attribution?.document_url ?? null;
  const posts = circular?.posts ?? [];

  return (
    <div className="space-y-6">
      <div>
        <Link className="text-sm text-slate-500 hover:text-slate-800" href={`/deputation/${id}`}>
          ← Circular
        </Link>
        <h1 className="mt-1 text-xl font-semibold">Assess: {circular ? circular.title || circular.attribution?.organization || "Deputation circular" : "…"}</h1>
        <p className="text-sm text-slate-600">
          Checks the serving-officer details of one profile version against the circular. Whether a post is analogous, the cooling-off
          period and the feeder-grade bar are always left to a human reviewer.
        </p>
      </div>

      {versions?.length === 0 ? (
        <p className="text-sm text-slate-600">
          No profile yet. <Link className="text-blue-700 underline" href="/profile">Create one</Link> first.
        </p>
      ) : (
        <form onSubmit={assess} className="flex flex-wrap items-center gap-3 rounded border border-slate-200 bg-white p-4 text-sm">
          <label className="flex items-center gap-2">
            <span className="text-slate-500">Profile version</span>
            <select className="rounded border border-slate-300 px-2 py-1" value={versionId} onChange={(e) => setVersionId(e.target.value)}>
              {versions?.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.version_number} ({fmtDate(v.created_at)})
                </option>
              ))}
            </select>
          </label>
          {posts.length > 1 && (
            <label className="flex items-center gap-2">
              <span className="text-slate-500">Post</span>
              <select className="rounded border border-slate-300 px-2 py-1" value={postIndex} onChange={(e) => setPostIndex(e.target.value)}>
                <option value="">All posts</option>
                {posts.map((p) => (
                  <option key={p.index} value={p.index}>
                    {termValue(p.name.value)}
                  </option>
                ))}
              </select>
            </label>
          )}
          <button disabled={running || !versionId} className="rounded bg-blue-700 px-3 py-1.5 font-medium text-white hover:bg-blue-800 disabled:opacity-50">
            {running ? "Checking…" : "Assess"}
          </button>
        </form>
      )}
      <ErrorNote error={error} />

      {result && (
        <>
          <p className="flex flex-wrap items-center gap-2 text-sm text-slate-600">
            As of {fmtDate(result.as_of)} · closing {result.closing_date ? fmtDate(result.closing_date) : "date not known"}
            {result.closing_status && <Badge value={result.closing_status} />}
          </p>
          <section className="space-y-3">
            <h2 className="font-medium">Posts</h2>
            {result.posts.map((p) => (
              <PostCard key={p.post_index} p={p} doc={doc} />
            ))}
            {!result.posts.length && <p className="text-sm text-slate-500">No post was assessed.</p>}
          </section>
          <section className="rounded border border-slate-200 bg-white p-4">
            <h2 className="mb-1 font-medium">Forwarding checklist</h2>
            <p className="text-xs text-slate-500">Only what the circular itself asks your office to send, with its words.</p>
            {result.forwarding_checklist.length ? (
              <ul className="mt-2 space-y-2">
                {result.forwarding_checklist.map((c, i) => (
                  <li key={`${c.code}-${i}`} className="border-t border-slate-100 pt-2 text-sm">
                    <span className="font-medium">{c.item}</span>
                    <TermCite page={c.page} quote={c.quote} docUrl={doc} />
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-2 text-sm text-slate-500">The circular states no forwarding documents the parser could read.</p>
            )}
          </section>
          <p className="text-xs text-slate-500">{result.disclaimer ?? "The official circular prevails."}</p>
        </>
      )}
    </div>
  );
}

function PostCard({ p, doc }: { p: PostAssessment; doc: string | null }) {
  return (
    <article className="rounded border border-slate-200 bg-white p-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{p.post}</span>
        <Badge value={p.status} />
        {p.pay_level != null && <span className="text-slate-500">Level {p.pay_level}</span>}
        {p.post_page != null && (
          <span className="ml-auto text-xs">
            <TermCite page={p.post_page} quote={null} docUrl={doc} />
          </span>
        )}
      </div>
      <ul className="mt-2 space-y-2">
        {p.outcomes.map((o, i) => (
          <li key={`${o.check}-${i}`} className="border-t border-slate-100 pt-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{o.check.replace(/_/g, " ")}</span>
              <Badge value={o.outcome} />
              {o.source_page != null && (
                <span className="ml-auto text-xs">
                  <TermCite page={o.source_page} quote={null} docUrl={doc} />
                </span>
              )}
            </div>
            <p>
              <span className="text-slate-500">Reason: </span>
              {o.rule}
            </p>
            <p>
              <span className="text-slate-500">Circular says: </span>
              {o.requirement}
            </p>
            <p>
              <span className="text-slate-500">Your profile: </span>
              {o.profile_evidence}
            </p>
            {o.note && <p className="text-xs text-amber-800">{o.note}</p>}
          </li>
        ))}
      </ul>
    </article>
  );
}
