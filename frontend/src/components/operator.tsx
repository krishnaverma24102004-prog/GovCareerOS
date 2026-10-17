"use client";
/**
 * Pieces shared by the /operator pages: the sub-navigation and the consent form the operator fills in with the
 * client present (the notice text, version and data items come from GET /operator/consent-notice).
 */
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { ErrorNote, errText } from "@/components/ui";
import { api } from "@/lib/api";
import type { ConsentIn, ConsentNotice } from "@/lib/types-operator";

const SUB_LINKS = [
  ["/operator", "Clients"],
  ["/operator/reports/new", "New report"],
  ["/operator/statement", "Statement"],
] as const;

export function OperatorNav() {
  const path = usePathname();
  return (
    <nav className="flex gap-4 border-b border-slate-200 pb-2 text-sm">
      {SUB_LINKS.map(([href, label]) => (
        <Link key={href} href={href} className={path === href ? "font-medium text-blue-700" : "text-slate-600 hover:text-slate-900"}>
          {label}
        </Link>
      ))}
      <span className="ml-auto rounded bg-amber-100 px-2 text-xs leading-5 text-amber-800">Billing: TEST MODE</span>
    </nav>
  );
}

const ITEM_LABELS: Record<string, string> = {
  full_name: "Name / Naam",
  personal_info: "Personal details (DOB, category, gender, contact) / Niji jaankari",
  education: "Education / Shiksha",
  experience: "Work experience / Anubhav",
  certificates: "Category certificates (OBC/SC/ST/EWS/PwBD) / Pramaan patra",
  career_details: "Skills, languages, other details / Anya jaankari",
};

export function ConsentForm({ submitLabel, onSubmit }: { submitLabel: string; onSubmit: (c: ConsentIn) => Promise<void> }) {
  const [notice, setNotice] = useState<ConsentNotice | null>(null);
  const [items, setItems] = useState<string[]>([]);
  const [present, setPresent] = useState(false);
  const [guardian, setGuardian] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<ConsentNotice>("/operator/consent-notice")
      .then((n) => {
        setNotice(n);
        setItems(n.data_items);
      })
      .catch((e) => setError(errText(e)));
  }, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!notice) return;
    setBusy(true);
    try {
      await onSubmit({ client_present: present, notice_version: notice.notice_version, data_items: items, guardian_name: guardian.trim() || null });
    } finally {
      setBusy(false);
    }
  }

  if (!notice) return <ErrorNote error={error} />;
  return (
    <form onSubmit={submit} className="space-y-3 text-sm">
      <div className="rounded border border-slate-200 bg-slate-50 p-3">
        <p className="mb-1 text-xs text-slate-500">Read this out to the client (notice {notice.notice_version}):</p>
        <p>{notice.text}</p>
      </div>
      <fieldset className="space-y-1">
        <legend className="text-xs text-slate-500">The client agrees to store only:</legend>
        {notice.data_items.map((item) => (
          <label key={item} className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={items.includes(item)}
              onChange={(e) => setItems(e.target.checked ? [...items, item] : items.filter((i) => i !== item))}
            />
            {ITEM_LABELS[item] ?? item}
          </label>
        ))}
      </fieldset>
      <label className="block">
        Parent / guardian name (only if the client is under 18)
        <input className="mt-1 block w-full rounded border border-slate-300 px-2 py-1" value={guardian} onChange={(e) => setGuardian(e.target.value)} />
      </label>
      <label className="flex items-center gap-2 font-medium">
        <input type="checkbox" checked={present} onChange={(e) => setPresent(e.target.checked)} />
        The client is present and agreed after hearing the notice / Client saamne hai aur sahmat hai
      </label>
      <button disabled={!present || items.length === 0 || busy} className="rounded bg-blue-700 px-3 py-1 text-white disabled:opacity-40">
        {submitLabel}
      </button>
    </form>
  );
}
