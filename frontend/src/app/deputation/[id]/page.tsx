"use client";
/**
 * One deputation circular (GET /deputation/circulars/{id}): every parsed term with its page and the circular's
 * own words, then each post with its pay level, mode, age limit, tenure and service clauses, and the documents
 * the forwarding office must send. Terms the parser could not read are simply absent; nothing is filled in.
 */
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { safeHref } from "@/components/applications";
import { TermCite, TermRow, termLabel, termValue } from "@/components/deputation";
import { Badge, ErrorNote, Shell, errText, fmtDate, useSession } from "@/components/ui";
import { api } from "@/lib/api";
import { asQuoted, asServiceClauses, type CircularDetail, type QuotedTerm } from "@/lib/types-deputation";

export default function DeputationCircularPage() {
  const user = useSession();
  const { id } = useParams<{ id: string }>();
  return <Shell user={user}>{user && id && <CircularView id={id} />}</Shell>;
}

/** Quoted terms of an object, skipping the keys handled elsewhere. */
function quotedTerms(obj: Record<string, unknown>, skip: string[]): [string, QuotedTerm][] {
  return Object.entries(obj)
    .filter(([k]) => !skip.includes(k))
    .map(([k, v]): [string, QuotedTerm | null] => [k, asQuoted(v)])
    .filter((e): e is [string, QuotedTerm] => e[1] !== null);
}

function CircularView({ id }: { id: string }) {
  const [c, setC] = useState<CircularDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api<CircularDetail>(`/deputation/circulars/${id}`)
      .then((d) => live && setC(d))
      .catch((e) => live && setError(errText(e)));
    return () => {
      live = false;
    };
  }, [id]);

  if (error) return <ErrorNote error={error} />;
  if (!c) return <p className="text-slate-500">Loading…</p>;

  const doc = c.attribution?.document_url ?? null;
  const terms = c.terms ?? {};
  const top = quotedTerms(terms, ["posts", "documents"]);
  const documents = quotedTerms((terms.documents as Record<string, unknown> | undefined) ?? {}, []);
  const posts = Array.isArray(terms.posts) ? terms.posts : [];

  return (
    <div className="space-y-6">
      <div>
        <Link className="text-sm text-slate-500 hover:text-slate-800" href="/deputation">
          ← Deputation circulars
        </Link>
        <h1 className="mt-1 flex flex-wrap items-center gap-3 text-xl font-semibold">
          {c.title || c.attribution?.organization || "Deputation circular"} {c.status && <Badge value={c.status} />}
        </h1>
        <p className="text-sm text-slate-500">
          {[c.attribution?.source_name, c.attribution?.organization, `${c.post_count} post(s)`, c.parsed_at && `parsed ${fmtDate(c.parsed_at)}`]
            .filter(Boolean)
            .join(" · ")}
        </p>
        <div className="mt-2 flex flex-wrap gap-3 text-sm">
          {safeHref(doc) && (
            <a className="text-blue-700 underline" href={safeHref(doc)!} target="_blank" rel="noreferrer">
              Official circular
            </a>
          )}
          <Link className="text-blue-700 underline" href={`/deputation/${c.id}/assess`}>
            Assess my profile against this circular
          </Link>
        </div>
      </div>

      <section className="rounded border border-slate-200 bg-white p-4">
        <h2 className="mb-1 font-medium">Terms of the circular</h2>
        {top.length ? (
          <ul className="space-y-2">
            {top.map(([k, t]) => (
              <TermRow key={k} name={k} term={t} docUrl={doc} />
            ))}
          </ul>
        ) : (
          <p className="text-sm text-slate-500">No circular-wide term could be read.</p>
        )}
      </section>

      <section>
        <h2 className="mb-2 font-medium">Posts</h2>
        {!posts.length && <p className="text-sm text-slate-500">No post could be read from this circular.</p>}
        <ul className="space-y-3">
          {posts.map((p, i) => {
            const name = asQuoted(p.name);
            const clauses = asServiceClauses(p.service);
            return (
              <li key={i} className="rounded border border-slate-200 bg-white p-4 text-sm">
                <div className="font-medium">{name ? termValue(name.value) : `Post ${i + 1}`}</div>
                {name && <TermCite page={name.page} quote={name.quote} docUrl={doc} />}
                <ul className="mt-2 space-y-2">
                  {quotedTerms(p, ["name", "service"]).map(([k, t]) => (
                    <TermRow key={k} name={k} term={t} docUrl={doc} />
                  ))}
                  {clauses.map((s, j) => (
                    <li key={`s-${j}`} className="border-t border-slate-100 pt-2">
                      <span className="text-slate-500">{termLabel("service")}: </span>
                      <span className="font-medium">
                        {s.years} years{s.regular ? " regular" : ""} service
                        {s.levels.length ? ` in Level ${s.levels.join("/")}` : " (levels not clearly stated)"}
                        {s.combined ? ", levels may be combined" : ""}
                      </span>
                      <TermCite page={s.page} quote={s.quote} docUrl={doc} />
                    </li>
                  ))}
                </ul>
              </li>
            );
          })}
        </ul>
      </section>

      <section className="rounded border border-slate-200 bg-white p-4">
        <h2 className="mb-1 font-medium">Documents the circular asks for</h2>
        {documents.length ? (
          <ul className="space-y-2">
            {documents.map(([k, t]) => (
              <li key={k} className="border-t border-slate-100 pt-2 text-sm">
                <span className="font-medium">{k.replace(/_/g, " ")}</span>
                <TermCite page={t.page} quote={t.quote} docUrl={doc} />
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-slate-500">The parser read no document requirement from this circular.</p>
        )}
      </section>

      <p className="text-xs text-slate-500">{c.disclaimer ?? "The official circular prevails."}</p>
    </div>
  );
}
