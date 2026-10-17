"use client";
/** Register or log in against /auth; the returned bearer token goes to sessionStorage only. */
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { ErrorNote, errText } from "@/components/ui";
import { api, API_URL, setToken } from "@/lib/api";
import type { Token } from "@/lib/types";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "register") {
        await api("/auth/register", { method: "POST", auth: false, body: { email, password, full_name: fullName || null } });
      }
      const token = await api<Token>("/auth/login", { method: "POST", auth: false, body: { email, password } });
      setToken(token.access_token);
      // A new account goes to the consent screen first (DPDP); it can be skipped there
      router.replace(mode === "register" ? "/privacy/consent" : "/matches");
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  }

  const input = "w-full rounded border border-slate-300 px-3 py-2";
  return (
    <main className="mx-auto mt-16 max-w-sm px-4">
      <h1 className="mb-1 text-2xl font-semibold">GovCareerOS</h1>
      <p className="mb-6 text-sm text-slate-500">API: {API_URL}</p>
      <div className="mb-4 flex gap-2 text-sm">
        {(["login", "register"] as const).map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => setMode(m)}
            className={`rounded px-3 py-1 ${mode === m ? "bg-blue-600 text-white" : "bg-slate-200"}`}
          >
            {m === "login" ? "Log in" : "Register"}
          </button>
        ))}
      </div>
      <form onSubmit={submit} className="space-y-3 rounded border border-slate-200 bg-white p-4">
        {mode === "register" && (
          <label className="block text-sm">
            Full name
            <input className={input} value={fullName} onChange={(e) => setFullName(e.target.value)} />
          </label>
        )}
        <label className="block text-sm">
          Email
          <input className={input} type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label className="block text-sm">
          Password {mode === "register" && <span className="text-slate-500">(at least 10 characters)</span>}
          <input
            className={input}
            type="password"
            required
            minLength={mode === "register" ? 10 : undefined}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <ErrorNote error={error} />
        <button disabled={busy} className="w-full rounded bg-blue-600 py-2 text-white disabled:opacity-50">
          {busy ? "…" : mode === "login" ? "Log in" : "Create account and log in"}
        </button>
      </form>
    </main>
  );
}
