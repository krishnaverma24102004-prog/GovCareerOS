"use client";
/**
 * Public notice check (POST /verify, no login): paste a recruitment notice's text, a link, or upload the PDF, and see
 * whether it is on an official source we track. The backend decides with fixed rules (never an LLM) and never fetches
 * the link; this page only shows its verdict, the stored official notices it matched (with the official document's
 * page and quote), the link checks, the red flags with the debunks they come from, and what was not read.
 */
import { useState, type FormEvent } from "react";

import { Badge, ErrorNote, Shell, errText, fmtDate, useOptionalSession } from "@/components/ui";
import { api } from "@/lib/api";
import type { DomainCheck, NoticeMatch, RedFlag, VerifyResult } from "@/lib/types-verify";

// Same limits as backend/services/notice_verifier.py; the backend enforces them, these only save a wasted upload
const MAX_PDF_BYTES = 10 * 1024 * 1024;
const MAX_TEXT_CHARS = 100_000;

export default function VerifyPage() {
  const { user } = useOptionalSession();
  return (
    <Shell user={user} publicPage>
      <VerifyView />
    </Shell>
  );
}

/** PDF viewers open at a page with the #page=N fragment. */
function pageLink(url: string, page: number | null): string {
  return page ? `${url}#page=${page}` : url;
}

const VERDICT_STYLE: Record<string, string> = {
  FOUND_ON_OFFICIAL_SOURCE: "border-green-200 bg-green-50 text-green-900",
  LOOKALIKE_DOMAIN: "border-red-200 bg-red-50 text-red-900",
  UNOFFICIAL_DOMAIN: "border-amber-200 bg-amber-50 text-amber-900",
  NOT_FOUND_ON_OFFICIAL_SOURCES: "border-slate-200 bg-slate-50 text-slate-900",
};

function VerifyView() {
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [ocr, setOcr] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<VerifyResult | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setResult(null);
    if (!text.trim() && !url.trim() && !file) {
      setError("Paste the notice text, a link, or choose a PDF.");
      return;
    }
    if (file && file.size > MAX_PDF_BYTES) {
      setError("The PDF is larger than 10 MB.");
      return;
    }
    const form = new FormData();
    if (text.trim()) form.append("text", text);
    if (url.trim()) form.append("url", url.trim());
    if (file) form.append("file", file);
    form.append("ocr", String(ocr));
    setBusy(true);
    try {
      setResult(await api<VerifyResult>("/verify", { form, auth: false }));
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  }

  const input = "w-full rounded border border-slate-300 px-3 py-2";
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Check a recruitment notice</h1>
        <p className="text-sm text-slate-600">
          Is a job notice, link or PDF on an official source we track? Fixed rules decide, not AI. Your link is only read as
          text; we never open it.
        </p>
      </div>

      <form onSubmit={submit} className="space-y-3 rounded border border-slate-200 bg-white p-4">
        <label className="block text-sm">
          Notice text
          <textarea
            className={`${input} h-36 font-mono text-xs`}
            maxLength={MAX_TEXT_CHARS}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Paste the notice, message or forward here"
          />
        </label>
        <label className="block text-sm">
          Link
          <input className={input} value={url} maxLength={2000} onChange={(e) => setUrl(e.target.value)} placeholder="e.g. https://…" />
        </label>
        <div className="flex flex-wrap items-center gap-4 text-sm">
          <label className="flex items-center gap-2">
            PDF (up to 10 MB)
            <input type="file" accept="application/pdf,.pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={ocr} onChange={(e) => setOcr(e.target.checked)} />
            Read scanned pages (OCR, slower; first few pages only)
          </label>
        </div>
        <ErrorNote error={error} />
        <button disabled={busy} className="rounded bg-blue-600 px-4 py-2 text-white disabled:opacity-50">
          {busy ? "Checking…" : "Check"}
        </button>
      </form>

      {result && <ResultView r={result} />}
    </div>
  );
}

function ResultView({ r }: { r: VerifyResult }) {
  return (
    <div className="space-y-6">
      <div className={`rounded border px-4 py-3 ${VERDICT_STYLE[r.verdict] || VERDICT_STYLE.NOT_FOUND_ON_OFFICIAL_SOURCES}`}>
        <div className="mb-1">
          <Badge value={r.verdict} />
        </div>
        <p>{r.message}</p>
      </div>

      {r.notes.length > 0 && (
        <section>
          <h2 className="mb-2 font-medium">Not fully checked</h2>
          <ul className="list-disc space-y-1 pl-5 text-sm text-slate-700">
            {r.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        </section>
      )}

      {r.url && (
        <section>
          <h2 className="mb-2 font-medium">The link</h2>
          <LinkCheck c={r.url} />
        </section>
      )}

      <section>
        <h2 className="mb-2 font-medium">Official notices it matches</h2>
        {r.matches.length === 0 ? (
          <p className="text-sm text-slate-500">None of the official notices we track matches.</p>
        ) : (
          <ul className="space-y-2">
            {r.matches.map((m) => (
              <MatchRow key={`${m.kind}-${m.vacancy_id || m.notice_id}`} m={m} />
            ))}
          </ul>
        )}
      </section>

      {r.links_in_text.length > 0 && (
        <section>
          <h2 className="mb-2 font-medium">Links in the text</h2>
          <ul className="space-y-2">
            {r.links_in_text.map((c) => (
              <li key={c.input}>
                <LinkCheck c={c} />
              </li>
            ))}
          </ul>
        </section>
      )}

      {r.red_flags.length > 0 && (
        <section>
          <h2 className="mb-2 font-medium">Red flags</h2>
          <ul className="space-y-2">
            {r.red_flags.map((f) => (
              <RedFlagRow key={f.code} f={f} />
            ))}
          </ul>
        </section>
      )}

      <p className="text-xs text-slate-500">
        {r.text_chars.toLocaleString("en-IN")} characters checked
        {r.pdf_pages !== null && `, ${r.pdf_pages_read} of ${r.pdf_pages} PDF pages read`}
        {r.advertisement_numbers.length > 0 && `; advertisement numbers seen: ${r.advertisement_numbers.join(", ")}`}. Checker{" "}
        {r.verifier_version}. &ldquo;Not found&rdquo; does not mean fake: check the organisation&apos;s official website before
        paying or sharing documents.
      </p>
    </div>
  );
}

function LinkCheck({ c }: { c: DomainCheck }) {
  return (
    <div className="rounded border border-slate-200 bg-white px-3 py-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono break-all">{c.host || c.input}</span>
        <Badge value={c.status} />
        {c.official_domain && (
          <span className="text-slate-500">
            {c.status === "OFFICIAL" ? "on" : "imitates"} {c.official_domain}
          </span>
        )}
      </div>
      {c.reasons.length > 0 && (
        <ul className="mt-1 list-disc pl-5 text-slate-600">
          {c.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
      {c.citation && (
        <>
          <blockquote className="mt-1 border-l-2 border-slate-300 pl-2 text-slate-700">
            &ldquo;{c.citation.quote}&rdquo;<span className="text-slate-500"> (page {c.citation.page})</span>
          </blockquote>
          {/^https?:\/\//i.test(c.citation.url) && (
            <a className="mt-1 inline-block break-all text-blue-700 underline" href={pageLink(c.citation.url, c.citation.page)} target="_blank" rel="noreferrer">
              {c.citation.document}
            </a>
          )}
        </>
      )}
    </div>
  );
}

function MatchRow({ m }: { m: NoticeMatch }) {
  return (
    <li className="rounded border border-slate-200 bg-white px-3 py-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{m.title}</span>
        {m.status && <Badge value={m.status} />}
        <span className="text-slate-500">
          {m.kind === "VACANCY" ? "vacancy" : "notice"} · {m.organization || m.source}
        </span>
      </div>
      <div className="mt-1 text-slate-600">
        Matched on {m.matched_on.join(", ")}
        {m.advertisement_number && ` · Advt. No. ${m.advertisement_number}`}
        {m.closing_date && ` · closes ${fmtDate(m.closing_date)}`}
      </div>
      {m.quote && (
        <blockquote className="mt-1 border-l-2 border-slate-300 pl-2 text-slate-700">
          &ldquo;{m.quote}&rdquo;{m.page && <span className="text-slate-500"> (page {m.page})</span>}
        </blockquote>
      )}
      {m.official_url && /^https?:\/\//i.test(m.official_url) && (
        <a className="mt-1 inline-block break-all text-blue-700 underline" href={pageLink(m.official_url, m.page)} target="_blank" rel="noreferrer">
          Official document
        </a>
      )}
    </li>
  );
}

function RedFlagRow({ f }: { f: RedFlag }) {
  return (
    <li className="rounded border border-red-200 bg-white px-3 py-2 text-sm">
      <div className="font-medium text-red-800">{f.description}</div>
      <blockquote className="mt-1 border-l-2 border-red-200 pl-2 text-slate-700">&ldquo;{f.quote}&rdquo;</blockquote>
      <div className="mt-1 text-slate-500">
        Reported in:{" "}
        {f.citations.map((c, i) => (
          <span key={c}>
            {i > 0 && ", "}
            <a className="break-all text-blue-700 underline" href={c} target="_blank" rel="noreferrer">
              {new URL(c).hostname}
            </a>
          </span>
        ))}
      </div>
    </li>
  );
}
