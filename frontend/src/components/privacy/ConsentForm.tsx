"use client";
/**
 * The consent notice with one checkbox per purpose (nothing pre-ticked), in English or Hindi. Each ticked purpose is
 * a separate POST /privacy/consents against the notice version shown, so the record says exactly what was agreed to.
 * A parent / guardian name is asked for when the person is under 18.
 */
import { useEffect, useState } from "react";

import { ErrorNote, errText } from "@/components/ui";
import { api } from "@/lib/api";
import type { ConsentNotice, ConsentRecord } from "@/lib/types-privacy";

export function ConsentForm({
  active = [],
  onDone,
  submitLabel = "Give consent",
}: {
  /** purposes already consented to under the current notice; shown as given */
  active?: string[];
  onDone: (given: ConsentRecord[]) => void;
  submitLabel?: string;
}) {
  const [notice, setNotice] = useState<ConsentNotice | null>(null);
  const [lang, setLang] = useState<"en" | "hi">("en");
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [minor, setMinor] = useState(false);
  const [guardian, setGuardian] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<ConsentNotice>("/privacy/notice", { auth: false })
      .then(setNotice)
      .catch((e) => setError(errText(e)));
  }, []);

  function toggle(purpose: string) {
    setChosen((s) => {
      const next = new Set(s);
      if (next.has(purpose)) next.delete(purpose);
      else next.add(purpose);
      return next;
    });
  }

  async function submit() {
    if (!notice) return;
    setError(null);
    if (minor && !guardian.trim()) {
      setError(lang === "hi" ? notice.minors.hi : notice.minors.en);
      return;
    }
    setBusy(true);
    const given: ConsentRecord[] = [];
    try {
      for (const purpose of Array.from(chosen)) {
        given.push(
          await api<ConsentRecord>("/privacy/consents", {
            method: "POST",
            body: { purpose, notice_version: notice.version, guardian_name: minor ? guardian.trim() : null },
          }),
        );
      }
      onDone(given);
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  if (!notice) return error ? <ErrorNote error={error} /> : <p className="text-slate-500">Loading the notice…</p>;
  const t = (b: { en: string; hi: string }) => (lang === "hi" ? b.hi : b.en);

  return (
    <div className="space-y-4" lang={lang}>
      <div className="flex items-center gap-2 text-sm">
        <span className="text-slate-500">Notice version {notice.version}</span>
        <span className="ml-auto" />
        {(["en", "hi"] as const).map((l) => (
          <button
            key={l}
            type="button"
            onClick={() => setLang(l)}
            className={`rounded px-2 py-0.5 ${lang === l ? "bg-blue-600 text-white" : "bg-slate-200"}`}
          >
            {l === "en" ? "English" : "हिन्दी"}
          </button>
        ))}
      </div>

      {notice.purposes.map((p) => {
        const already = active.includes(p.purpose);
        return (
          <label key={p.purpose} className="block rounded border border-slate-200 bg-white p-3 text-sm">
            <span className="flex items-start gap-2">
              <input
                type="checkbox"
                className="mt-1"
                disabled={already}
                checked={already || chosen.has(p.purpose)}
                onChange={() => toggle(p.purpose)}
              />
              <span>
                <span className="font-medium">{p.purpose.replace(/_/g, " ")}</span>
                {already && <span className="ml-2 text-xs text-green-700">(given)</span>}
                <span className="mt-1 block text-slate-700">{t(p)}</span>
                <span className="mt-1 block text-slate-500">
                  {lang === "hi" ? "जानकारी: " : "Data: "}
                  {p.data_items.map((i) => t(i)).join("; ")}
                </span>
              </span>
            </span>
          </label>
        );
      })}

      <div className="rounded border border-slate-200 bg-slate-50 p-3 text-sm text-slate-700">
        <p>{t(notice.withdraw)}</p>
        <p className="mt-2">{t(notice.grievance)}</p>
      </div>

      <div className="space-y-2 text-sm">
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={minor} onChange={(e) => setMinor(e.target.checked)} />
          {lang === "hi" ? "मेरी आयु 18 वर्ष से कम है" : "I am under 18"}
        </label>
        {minor && (
          <label className="block">
            {t(notice.minors)}
            <input
              className="mt-1 w-full rounded border border-slate-300 px-3 py-2"
              value={guardian}
              onChange={(e) => setGuardian(e.target.value)}
              placeholder={lang === "hi" ? "माता-पिता / अभिभावक का नाम" : "Parent / guardian name"}
            />
          </label>
        )}
      </div>

      <ErrorNote error={error} />
      <button
        type="button"
        disabled={busy || chosen.size === 0}
        onClick={submit}
        className="rounded bg-blue-600 px-4 py-2 text-sm text-white disabled:opacity-50"
      >
        {busy ? "…" : submitLabel}
      </button>
    </div>
  );
}
