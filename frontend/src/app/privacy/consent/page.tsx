"use client";
/**
 * Consent screen shown right after signup (and linked from the profile when consent is missing). Skipping is
 * allowed: without ELIGIBILITY_CHECK consent the profile simply cannot hold category, date of birth, disability,
 * certificates or service details, and no eligibility check runs.
 */
import Link from "next/link";
import { useRouter } from "next/navigation";

import { ConsentForm } from "@/components/privacy/ConsentForm";
import { Shell, useSession } from "@/components/ui";

export default function ConsentPage() {
  const user = useSession();
  const router = useRouter();
  return (
    <Shell user={user}>
      <div className="max-w-2xl space-y-4">
        <h1 className="text-xl font-semibold">Your consent / आपकी सहमति</h1>
        <p className="text-sm text-slate-600">
          Choose what we may use your information for. You can change this at any time on the{" "}
          <Link href="/privacy" className="underline">
            Privacy
          </Link>{" "}
          page. Eligibility results are guidance only; the official notification prevails.
        </p>
        <ConsentForm onDone={() => router.replace("/profile")} submitLabel="Agree to the ticked purposes" />
        <button type="button" onClick={() => router.replace("/profile")} className="text-sm text-slate-600 underline">
          Not now
        </button>
      </div>
    </Shell>
  );
}
