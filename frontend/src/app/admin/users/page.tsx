"use client";
/**
 * Reviewer administration: who may review, and the audit log of every reviewer decision.
 * The backend refuses to remove the last reviewer.
 */
import { useCallback, useEffect, useState } from "react";

import { Badge, ErrorNote, Shell, errText, fmtDate, useSession } from "@/components/ui";
import { api } from "@/lib/api";
import type { AdminUser, AuditRow, Paged, User } from "@/lib/types";

const AUDIT_PAGE = 100;
const OBJECT_TYPES = ["", "DRAFT", "NOTICE", "FACT", "USER"] as const;

export default function AdminUsersPage() {
  const user = useSession();
  return (
    <Shell user={user}>
      {user &&
        (user.is_reviewer ? (
          <div className="space-y-8">
            <Users me={user} />
            <Audit />
          </div>
        ) : (
          <p className="text-slate-500">Reviewer administration is for reviewers only.</p>
        ))}
    </Shell>
  );
}

function Users({ me }: { me: User }) {
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setUsers(await api<AdminUser[]>("/admin/users?limit=500"));
    } catch (e) {
      setError(errText(e));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function setReviewer(u: AdminUser, value: boolean) {
    setError(null);
    if (u.id === me.id && !value && !window.confirm("Remove your own reviewer role? You will lose access to this page.")) return;
    try {
      await api(`/admin/users/${u.id}/reviewer`, { method: "POST", body: { is_reviewer: value } });
      await load();
    } catch (e) {
      setError(errText(e));
    }
  }

  return (
    <section className="space-y-3">
      <h1 className="text-xl font-semibold">Users</h1>
      <ErrorNote error={error} />
      <table className="w-full rounded border border-slate-200 bg-white text-sm">
        <thead className="bg-slate-50 text-left text-xs text-slate-500">
          <tr>
            <th className="px-3 py-1">Email</th>
            <th className="px-3 py-1">Name</th>
            <th className="px-3 py-1">Active</th>
            <th className="px-3 py-1">Reviewer</th>
            <th className="px-3 py-1" />
          </tr>
        </thead>
        <tbody>
          {users?.map((u) => (
            <tr key={u.id} className="border-t border-slate-100">
              <td className="px-3 py-1">
                {u.email}
                {u.id === me.id && <span className="ml-1 text-xs text-slate-500">(you)</span>}
              </td>
              <td className="px-3 py-1">{u.full_name || "—"}</td>
              <td className="px-3 py-1">{u.is_active ? "yes" : "no"}</td>
              <td className="px-3 py-1">{u.is_reviewer ? <Badge value="REVIEWER" /> : "—"}</td>
              <td className="px-3 py-1 text-right">
                <button className="rounded border border-slate-300 px-2 py-0.5 text-xs" onClick={() => setReviewer(u, !u.is_reviewer)}>
                  {u.is_reviewer ? "Remove reviewer" : "Make reviewer"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function Audit() {
  const [objectType, setObjectType] = useState<string>("");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<Paged<AuditRow> | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const params = new URLSearchParams({ limit: String(AUDIT_PAGE), offset: String(offset) });
    if (objectType) params.set("object_type", objectType);
    api<Paged<AuditRow>>(`/admin/audit?${params}`)
      .then((p) => {
        setPage(p);
        setError(null);
      })
      .catch((e) => setError(errText(e)));
  }, [objectType, offset]);

  const total = page?.total ?? 0;
  return (
    <section className="space-y-3">
      <div className="flex items-center gap-3">
        <h2 className="text-lg font-semibold">Audit log</h2>
        <select
          className="rounded border border-slate-300 px-2 py-1 text-sm"
          value={objectType}
          onChange={(e) => {
            setObjectType(e.target.value);
            setOffset(0);
          }}
        >
          {OBJECT_TYPES.map((t) => (
            <option key={t} value={t}>
              {t ? t.toLowerCase() : "all"}
            </option>
          ))}
        </select>
        <span className="text-sm text-slate-500">{total} entries</span>
      </div>
      <ErrorNote error={error} />
      <table className="w-full rounded border border-slate-200 bg-white text-sm">
        <thead className="bg-slate-50 text-left text-xs text-slate-500">
          <tr>
            <th className="px-3 py-1">When</th>
            <th className="px-3 py-1">Who</th>
            <th className="px-3 py-1">Action</th>
            <th className="px-3 py-1">Object</th>
            <th className="px-3 py-1">Note</th>
            <th className="px-3 py-1">Change</th>
          </tr>
        </thead>
        <tbody>
          {page?.items.map((a) => (
            <tr key={a.id} className="border-t border-slate-100 align-top">
              <td className="whitespace-nowrap px-3 py-1 text-xs">{fmtDate(a.at, true)}</td>
              <td className="px-3 py-1">{a.actor_email}</td>
              <td className="px-3 py-1 font-mono text-xs">{a.action}</td>
              <td className="px-3 py-1 font-mono text-xs">
                {a.object_type} {a.object_id.slice(0, 8)}
              </td>
              <td className="px-3 py-1">{a.note || "—"}</td>
              <td className="px-3 py-1 font-mono text-xs text-slate-500">{a.detail ? JSON.stringify(a.detail) : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {total > AUDIT_PAGE && (
        <div className="flex items-center gap-3 text-sm">
          <button disabled={offset === 0} className="disabled:opacity-40" onClick={() => setOffset(Math.max(0, offset - AUDIT_PAGE))}>
            ← newer
          </button>
          <button disabled={offset + AUDIT_PAGE >= total} className="disabled:opacity-40" onClick={() => setOffset(offset + AUDIT_PAGE)}>
            older →
          </button>
        </div>
      )}
    </section>
  );
}
