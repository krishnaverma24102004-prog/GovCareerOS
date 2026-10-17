"""
Weekly job: write a cited digest for every active user with a profile and active ALERTS consent; email it only with
--send.

    $env:PYTHONPATH="C:\\Users\\krishna"
    python govcareeros/scripts/send_digests.py          # write storage/digests/<user_id>/<date>.html (+ .txt, .json)
    python govcareeros/scripts/send_digests.py --send   # also email each digest (needs EMAIL_USERNAME / EMAIL_PASSWORD)

--send emails only users with an active ALERTS consent (services/consent.py); everyone else gets the digest file
only. Each output line names the user by id, never by email address: in the compose stack this output is the
scheduler container's log, which does not pass through the JSON log redaction.

Run scripts/run_radar.py first so official data is fresh; the digest itself only evaluates vacancies
that have no verdict on their current inputs.
"""
import asyncio
import re
import sys

from govcareeros.backend.database.session import AsyncSessionLocal, engine
from govcareeros.backend.services import digest, mailer

_EMAIL = re.compile(r"(?<![\w.%+-])[\w.%+-]{1,64}@[\w.-]{1,255}")

def without_addresses(text: str) -> str:
    """An SMTP error usually quotes the recipient (SMTPRecipientsRefused is a dict keyed by address)."""
    return _EMAIL.sub("[address]", text)

async def main(send: bool) -> int:
    if send and not mailer.is_configured():
        print("--send given but EMAIL_USERNAME / EMAIL_PASSWORD are not set; writing files only")
        send = False
    failures = emailed = no_consent = skipped = 0
    async with AsyncSessionLocal() as session:
        runs = await digest.run_all(session)
        await session.commit()
        # Plain values first: a failed send rolls back, which expires every loaded row
        summary = [(run.id, run.user_id, f"{run.user_id}: {run.item_count} item(s) {run.item_counts}, "
                    f"{run.omitted_count} left out -> {digest.run_file(run)}") for run in runs]
        for run_id, user_id, line in summary:
            email = (await session.get(digest.User, user_id)).email if send else None
            if send and not email:
                skipped += 1
                line += "; not emailed (operator-managed client without an email)"
            elif send and not await digest.may_email(session, user_id):
                no_consent += 1
                line += f"; not emailed: no active {digest.EMAIL_CONSENT_PURPOSE} consent"
            elif send:
                try:
                    await digest.send_run(session, await session.get(digest.DigestRun, run_id), email)
                    await session.commit()
                    emailed += 1
                    line += "; emailed"
                except Exception as e:  # one bad address must not stop the others
                    await session.rollback()
                    failures += 1
                    line += f"; email FAILED: {type(e).__name__}: {without_addresses(str(e))}"
            print(line)
        print(f"{len(summary)} digest(s) written" + (f", {emailed} emailed, {failures} failed, {no_consent} without "
                                                     f"{digest.EMAIL_CONSENT_PURPOSE} consent, {skipped} without an email" if send else ""))
    await engine.dispose()
    return 1 if failures else 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main("--send" in sys.argv)))
