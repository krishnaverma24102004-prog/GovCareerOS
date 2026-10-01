"use client";
/**
 * Reviewer console: one prioritised queue of LLM drafts waiting for a decision and notices nobody has dealt
 * with yet, so one person can clear a backlog of hundreds quickly.
 *
 * Keys: j / k next / previous · x select · a approve (opens confirm) · r reject (draft) or "not required" (notice)
 * · s skip · o open the official link · Esc closes a dialog.
 *
 * Notices can be handled in bulk (skip / not required / requeue). Drafts can be rejected in bulk but approved
 * only one at a time, in the side-by-side view, through a confirm dialog; the backend has no bulk approve.
 * Non-reviewers see the read-only draft list instead.
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { Dialog, typingIn } from "@/components/Dialog";
import { DraftList } from "@/components/DraftList";
import { DraftReview, type DraftCommand } from "@/components/DraftReview";
import { Badge, ErrorNote, Shell, errText, fmtDate, useSession } from "@/components/ui";
import { api } from "@/lib/api";
import type { Paged, QueueFacets, QueueItem } from "@/lib/types";

const PAGE = 50;
type NoticeAction = "SKIP" | "NOT_REQUIRED" | "REQUEUE";
const ACTION_LABEL: Record<NoticeAction, string> = { SKIP: "Skip", NOT_REQUIRED: "Not required", REQUEUE: "Requeue" };
type Filters = { source: string; kind: string; status: string; type: string; q: string };
type BulkDialog = { kind: "notices"; action: NoticeAction; ids: string[] } | { kind: "drafts"; ids: string[] } | null;

export default function ReviewPage() {
  const user = useSession();
  return (
    <Shell user={user} wide={Boolean(user?.is_reviewer)}>
      {user &&
        (user.is_reviewer ? (
          <Console />
        ) : (
          <div className="space-y-3">
            <p className="text-sm text-slate-500">Read-only: your account is not a reviewer, so the reviewer console is not available.</p>
            <DraftList user={user} />
          </div>
        ))}
    </Shell>
  );
}

function priorityLabel(item: QueueItem): string {
  if (item.priority !== null && item.priority !== undefined) return String(item.priority);
  return item.notice_kind === "VACANCY" || item.notice_kind === "CORRIGENDUM" ? "kind" : "";
}

function Console() {
  const [facets, setFacets] = useState<QueueFacets | null>(null);
  const [filters, setFilters] = useState<Filters>({ source: "", kind: "", status: "", type: "", q: "" });
  const [qInput, setQInput] = useState("");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<Paged<QueueItem> | null>(null);
  const [cursor, setCursor] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [command, setCommand] = useState<DraftCommand>(null);
  const [bulk, setBulk] = useState<BulkDialog>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);

  useEffect(() => {
    api<QueueFacets>("/review/queue/facets").then(setFacets).catch((e) => setError(errText(e)));
  }, []);

  const load = useCallback(async () => {
    const params = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
    for (const [k, v] of Object.entries(filters)) if (v) params.set(k, v);
    try {
      const p = await api<Paged<QueueItem>>(`/review/queue?${params}`);
      setPage(p);
      setCursor((c) => Math.max(0, Math.min(c, p.items.length - 1)));
      setError(null);
    } catch (e) {
      setError(errText(e));
    }
  }, [filters, offset]);

  useEffect(() => {
    load();
  }, [load]);

  const items = useMemo(() => page?.items ?? [], [page]);
  const current = items[cursor] as QueueItem | undefined;
  const selectedItems = items.filter((i) => selected.has(i.id));
  const selectedNotices = selectedItems.filter((i) => i.type === "NOTICE").map((i) => i.id);
  const selectedDrafts = selectedItems.filter((i) => i.type === "DRAFT").map((i) => i.id);

  function setFilter(k: keyof Filters, v: string) {
    setFilters((f) => ({ ...f, [k]: v }));
    setOffset(0);
    setCursor(0);
    setSelected(new Set());
  }

  function toggle(id: string) {
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  }

  // A shortcut acts on the selection when the current row is part of it, else on the current row alone
  const noticeTargets = useCallback(
    (item: QueueItem) => (selected.has(item.id) && selectedNotices.length ? selectedNotices : [item.id]),
    [selected, selectedNotices],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (bulk || typingIn(e.target) || e.ctrlKey || e.metaKey || e.altKey) return;
      if (document.querySelector("[role=dialog]")) return;
      const k = e.key;
      if (k === "j") setCursor((c) => Math.min(c + 1, items.length - 1));
      else if (k === "k") setCursor((c) => Math.max(c - 1, 0));
      else if (!current) return;
      else if (k === "x") toggle(current.id);
      else if (k === "o" && current.url) window.open(current.url, "_blank", "noopener,noreferrer");
      else if (k === "a" && current.type === "DRAFT") setCommand((c) => ({ action: "approve", n: (c?.n ?? 0) + 1 }));
      else if (k === "r" && current.type === "DRAFT") setCommand((c) => ({ action: "reject", n: (c?.n ?? 0) + 1 }));
      else if (k === "r" && current.type === "NOTICE") setBulk({ kind: "notices", action: "NOT_REQUIRED", ids: noticeTargets(current) });
      else if (k === "s" && current.type === "NOTICE") setBulk({ kind: "notices", action: "SKIP", ids: noticeTargets(current) });
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [bulk, current, items.length, noticeTargets]);

  useEffect(() => {
    document.getElementById(`row-${cursor}`)?.scrollIntoView({ block: "nearest" });
  }, [cursor]);

  async function runBulk() {
    if (!bulk) return;
    setError(null);
    if (bulk.kind === "drafts" && !note.trim()) {
      setError("A note is required to reject drafts.");
      return;
    }
    setBusy(true);
    try {
      if (bulk.kind === "notices") {
        const r = await api<{ updated: number }>("/review/notices/bulk", {
          method: "POST",
          body: { notice_ids: bulk.ids, action: bulk.action, note: note.trim() || null },
        });
        setFlash(`${ACTION_LABEL[bulk.action]}: ${r.updated} notice(s)`);
      } else {
        const r = await api<{ rejected: number }>("/review/drafts/bulk-reject", { method: "POST", body: { draft_ids: bulk.ids, note: note.trim() } });
        setFlash(`Rejected ${r.rejected} draft(s)`);
      }
      setBulk(null);
      setNote("");
      setSelected(new Set());
      await load();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  const select = (k: keyof Filters, label: string, options: string[]) => (
    <label className="text-xs text-slate-500">
      {label}
      <select className="ml-1 rounded border border-slate-300 px-1 py-0.5 text-sm text-slate-900" value={filters[k]} onChange={(e) => setFilter(k, e.target.value)}>
        <option value="">all</option>
        {options.map((o) => (
          <option key={o} value={o}>
            {o.replace(/_/g, " ").toLowerCase()}
          </option>
        ))}
      </select>
    </label>
  );

  const total = page?.total ?? 0;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">Review console</h1>
        <span className="text-sm text-slate-500">{total} waiting</span>
        {select("type", "type", ["DRAFT", "NOTICE"])}
        {select("source", "source", facets?.sources ?? [])}
        {select("kind", "kind", facets?.kinds ?? [])}
        {select("status", "status", facets?.statuses ?? [])}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setFilter("q", qInput.trim());
          }}
        >
          <input className="rounded border border-slate-300 px-2 py-0.5 text-sm" placeholder="search titles" value={qInput} onChange={(e) => setQInput(e.target.value)} />
        </form>
        <span className="ml-auto text-xs text-slate-500">j/k move · x select · a approve · r reject · s skip · o open</span>
      </div>
      <ErrorNote error={error} />
      {flash && (
        <p className="rounded border border-green-200 bg-green-50 px-3 py-1 text-sm text-green-800" onClick={() => setFlash(null)}>
          {flash}
        </p>
      )}

      {selected.size > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded border border-slate-200 bg-slate-50 px-3 py-2 text-sm">
          <span>{selected.size} selected</span>
          {selectedNotices.length > 0 &&
            (Object.keys(ACTION_LABEL) as NoticeAction[]).map((a) => (
              <button key={a} className="rounded border border-slate-300 bg-white px-2 py-0.5" onClick={() => setBulk({ kind: "notices", action: a, ids: selectedNotices })}>
                {ACTION_LABEL[a]} {selectedNotices.length} notice(s)
              </button>
            ))}
          {selectedDrafts.length > 0 && (
            <button className="rounded border border-red-300 bg-white px-2 py-0.5 text-red-800" onClick={() => setBulk({ kind: "drafts", ids: selectedDrafts })}>
              Reject {selectedDrafts.length} draft(s)
            </button>
          )}
          <button className="ml-auto text-slate-500" onClick={() => setSelected(new Set())}>
            clear
          </button>
        </div>
      )}

      <div className="max-h-[38vh] overflow-y-auto rounded border border-slate-200 bg-white">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-slate-50 text-left text-xs text-slate-500">
            <tr>
              <th className="w-8 px-2 py-1">
                <input
                  type="checkbox"
                  aria-label="select all on this page"
                  checked={items.length > 0 && items.every((i) => selected.has(i.id))}
                  onChange={(e) => setSelected(e.target.checked ? new Set(items.map((i) => i.id)) : new Set())}
                />
              </th>
              <th className="px-2 py-1">Prio</th>
              <th className="px-2 py-1">Type</th>
              <th className="px-2 py-1">Title</th>
              <th className="px-2 py-1">Source</th>
              <th className="px-2 py-1">Kind</th>
              <th className="px-2 py-1">Status</th>
              <th className="px-2 py-1">First seen</th>
            </tr>
          </thead>
          <tbody>
            {items.map((i, n) => (
              <tr
                key={i.id}
                id={`row-${n}`}
                onClick={() => setCursor(n)}
                className={`cursor-pointer border-t border-slate-100 ${n === cursor ? "bg-blue-50 outline outline-1 outline-blue-300" : "hover:bg-slate-50"}`}
              >
                <td className="px-2 py-1" onClick={(e) => e.stopPropagation()}>
                  <input type="checkbox" aria-label="select" checked={selected.has(i.id)} onChange={() => toggle(i.id)} />
                </td>
                <td className="px-2 py-1 text-xs text-slate-500">{priorityLabel(i)}</td>
                <td className="px-2 py-1 text-xs">{i.type === "DRAFT" ? `draft · ${i.vacancies} vac` : i.document_type}</td>
                <td className="max-w-md truncate px-2 py-1" title={i.title ?? ""}>
                  {i.title || "(untitled)"}
                </td>
                <td className="px-2 py-1">{i.source}</td>
                <td className="px-2 py-1 text-xs">{i.notice_kind}</td>
                <td className="px-2 py-1">
                  <Badge value={i.status} />
                </td>
                <td className="whitespace-nowrap px-2 py-1 text-xs text-slate-500">{fmtDate(i.first_seen_at ?? i.created_at)}</td>
              </tr>
            ))}
            {page && items.length === 0 && (
              <tr>
                <td colSpan={8} className="px-2 py-4 text-center text-slate-500">
                  Nothing waiting.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="flex items-center gap-3 text-sm">
        <button disabled={offset === 0} className="disabled:opacity-40" onClick={() => (setOffset(Math.max(0, offset - PAGE)), setCursor(0))}>
          ← prev
        </button>
        <span className="text-slate-500">
          {total ? offset + 1 : 0}–{Math.min(offset + PAGE, total)} of {total}
        </span>
        <button disabled={offset + PAGE >= total} className="disabled:opacity-40" onClick={() => (setOffset(offset + PAGE), setCursor(0))}>
          next →
        </button>
      </div>

      {current?.type === "DRAFT" && current.draft_id && (
        <DraftReview
          draftId={current.draft_id}
          command={command}
          onDecided={(msg) => {
            setFlash(msg);
            load();
          }}
        />
      )}
      {current?.type === "NOTICE" && (
        <NoticePanel item={current} onAction={(action) => setBulk({ kind: "notices", action, ids: noticeTargets(current) })} />
      )}

      {bulk && (
        <Dialog
          title={bulk.kind === "drafts" ? `Reject ${bulk.ids.length} draft(s)` : `${ACTION_LABEL[bulk.action]} ${bulk.ids.length} notice(s)`}
          onClose={() => setBulk(null)}
        >
          <p className="text-sm text-slate-600">
            {bulk.kind === "drafts"
              ? "Every undecided vacancy in these drafts is rejected. Already approved vacancies stay."
              : bulk.action === "REQUEUE"
                ? "The notices go back to the status discovery gives a new notice, so the pipeline picks them up again."
                : "The notices leave the queue. You can find them again with the status filter and requeue them."}
          </p>
          <input
            autoFocus
            className="w-full rounded border border-slate-300 px-2 py-1 text-sm"
            placeholder={bulk.kind === "drafts" ? "Why (required)" : "Note (optional)"}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && runBulk()}
          />
          <ErrorNote error={error} />
          <div className="flex justify-end gap-2">
            <button className="px-3 py-1 text-sm" onClick={() => setBulk(null)}>
              Cancel
            </button>
            <button disabled={busy} onClick={runBulk} className="rounded bg-slate-800 px-3 py-1 text-sm text-white disabled:opacity-40">
              Confirm
            </button>
          </div>
        </Dialog>
      )}
    </div>
  );
}

function NoticePanel({ item, onAction }: { item: QueueItem; onAction: (a: NoticeAction) => void }) {
  return (
    <div className="space-y-2 rounded border border-slate-200 bg-white p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge value={item.status} />
        <span className="font-medium">{item.title || "(untitled)"}</span>
        <span className="text-slate-500">
          {item.source} · {item.notice_kind} · {item.document_type} · first seen {fmtDate(item.first_seen_at, true)}
        </span>
      </div>
      {item.url && (
        <a className="break-all text-blue-700 underline" href={item.url} target="_blank" rel="noreferrer">
          {item.url}
        </a>
      )}
      {item.review_note && (
        <p className="text-slate-600">
          Last note: {item.review_note} ({fmtDate(item.reviewed_at, true)})
        </p>
      )}
      <div className="flex gap-2">
        {(Object.keys(ACTION_LABEL) as NoticeAction[]).map((a) => (
          <button key={a} className="rounded border border-slate-300 px-2 py-0.5" onClick={() => onAction(a)}>
            {ACTION_LABEL[a]}
            {a === "SKIP" && <kbd className="ml-1 text-xs text-slate-400">s</kbd>}
            {a === "NOT_REQUIRED" && <kbd className="ml-1 text-xs text-slate-400">r</kbd>}
          </button>
        ))}
      </div>
    </div>
  );
}
