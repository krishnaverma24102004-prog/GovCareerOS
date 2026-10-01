"use client";
/**
 * Small shared pieces: the signed-in guard, the nav bar, status badges and date formatting.
 * useSession() is the only place that decides "signed in or not": a missing token or a 401 from
 * /auth/me sends the user to /login.
 */
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { api, ApiError, getToken, setToken } from "@/lib/api";
import type { User } from "@/lib/types";

export function useSession(): User | null {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    api<User>("/auth/me")
      .then(setUser)
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) router.replace("/login");
      });
  }, [router]);
  return user;
}

const LINKS = [
  ["/matches", "Matches"],
  ["/profile", "Profile"],
  ["/alerts", "Alerts"],
  ["/review", "Review"],
] as const;

// Reviewer console pages; the backend refuses them to everyone else anyway
const REVIEWER_LINKS = [
  ["/review/facts", "Facts"],
  ["/admin/users", "Users"],
] as const;

export function Shell({ user, children, wide = false }: { user: User | null; children: ReactNode; wide?: boolean }) {
  const path = usePathname();
  const links = user?.is_reviewer ? [...LINKS, ...REVIEWER_LINKS] : LINKS;
  // The most specific link wins, so /review/facts does not also light up /review
  const active = links.map(([href]) => href).filter((href) => path === href || path?.startsWith(href + "/")).sort((a, b) => b.length - a.length)[0];
  const router = useRouter();
  async function logout() {
    await api("/auth/logout", { method: "POST" }).catch(() => undefined);
    setToken(null);
    router.replace("/login");
  }
  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <nav className={`mx-auto flex ${wide ? "max-w-screen-2xl" : "max-w-5xl"} flex-wrap items-center gap-4 px-4 py-3`}>
          <span className="font-semibold">GovCareerOS</span>
          {links.map(([href, label]) => (
            <Link
              key={href}
              href={href}
              className={href === active ? "font-medium text-blue-700" : "text-slate-600 hover:text-slate-900"}
            >
              {label}
            </Link>
          ))}
          <span className="ml-auto text-sm text-slate-500">{user?.email}</span>
          <button onClick={logout} className="text-sm text-slate-600 hover:text-slate-900">
            Log out
          </button>
        </nav>
      </header>
      <main className={`mx-auto ${wide ? "max-w-screen-2xl" : "max-w-5xl"} px-4 py-6`}>{user ? children : <p className="text-slate-500">Loading…</p>}</main>
    </div>
  );
}

const STATUS_STYLE: Record<string, string> = {
  ELIGIBLE: "bg-green-100 text-green-800",
  MET: "bg-green-100 text-green-800",
  APPROVED: "bg-green-100 text-green-800",
  ACCEPTED: "bg-green-100 text-green-800",
  CURRENT: "bg-blue-100 text-blue-800",
  CONDITIONAL: "bg-amber-100 text-amber-800",
  MANUAL_REVIEW: "bg-sky-100 text-sky-800",
  UNKNOWN: "bg-sky-100 text-sky-800",
  PENDING_REVIEW: "bg-sky-100 text-sky-800",
  INELIGIBLE: "bg-red-100 text-red-800",
  NOT_MET: "bg-red-100 text-red-800",
  REJECTED: "bg-red-100 text-red-800",
  FAILED: "bg-red-100 text-red-800",
  DRAFT_FAILED: "bg-red-100 text-red-800",
  VERIFIED: "bg-green-100 text-green-800",
  UNVERIFIED: "bg-amber-100 text-amber-800",
  SKIPPED: "bg-slate-200 text-slate-700",
  NOT_REQUIRED: "bg-slate-200 text-slate-700",
  NOT_STATED: "bg-slate-100 text-slate-500",
  REVIEWER: "bg-green-100 text-green-800",
};

export function Badge({ value }: { value: string }) {
  return (
    <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${STATUS_STYLE[value] || "bg-slate-100 text-slate-700"}`}>
      {value.replace(/_/g, " ")}
    </span>
  );
}

export function ErrorNote({ error }: { error: string | null }) {
  return error ? <p className="my-3 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{error}</p> : null;
}

export function fmtDate(iso: string | null | undefined, withTime = false): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return withTime ? d.toLocaleString("en-IN") : d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

export function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}
