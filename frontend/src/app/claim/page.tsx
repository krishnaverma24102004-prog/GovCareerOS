"use client";
/**
 * Take over an account an operator (cyber cafe / CSC) created for you: the one-time code from the operator, your own
 * email and a password. After that you log in like anyone else.
 */
import Link from "next/link";
import { useState, type FormEvent } from "react";

import { ErrorNote, errText } from "@/components/ui";
import { api } from "@/lib/api";
import type { User } from "@/lib/types";

export default function ClaimPage() {
  const [token, setToken] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<User | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      setDone(await api<User>("/auth/claim", { method: "POST", auth: false, body: { token: token.trim(), email, password } }));
    } catch (err) {
      setError(errText(err));
    }
  }

  return (
    <main className="mx-auto max-w-sm space-y-4 px-4 py-12">
      <h1 className="text-xl font-semibold">Claim your account</h1>
      {done ? (
        <p className="text-sm">
          Done. <Link className="text-blue-700" href="/login">Log in</Link> as {done.email}.
        </p>
      ) : (
        <form onSubmit={submit} className="space-y-3 text-sm">
          <label className="block">
            One-time code from the operator
            <input className="mt-1 block w-full rounded border border-slate-300 px-2 py-1 font-mono" value={token} onChange={(e) => setToken(e.target.value)} required />
          </label>
          <label className="block">
            Your email
            <input type="email" className="mt-1 block w-full rounded border border-slate-300 px-2 py-1" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </label>
          <label className="block">
            New password (at least 10 characters)
            <input
              type="password"
              minLength={10}
              className="mt-1 block w-full rounded border border-slate-300 px-2 py-1"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>
          <ErrorNote error={error} />
          <button className="rounded bg-blue-700 px-3 py-1 text-white">Claim account</button>
        </form>
      )}
    </main>
  );
}
