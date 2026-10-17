"""
Weekly job: write a cited digest for every active user with a profile; email it only with --send.

    $env:PYTHONPATH="C:\\Users\\krishna"
    python govcareeros/scripts/send_digests.py          # write storage/digests/<user_id>/<date>.html (+ .txt, .json)
    python govcareeros/scripts/send_digests.py --send   # also email each digest (needs EMAIL_USERNAME / EMAIL_PASSWORD)

Run scripts/run_radar.py first so official data is fresh; the digest itself only evaluates vacancies
that have no verdict on their current inputs.
"""
import asyncio
import sys

from govcareeros.backend.database.session import AsyncSessionLocal, engine
from govcareeros.backend.services import digest, mailer

async def main(send: bool) -> int:
    if send and not mailer.is_configured():
        print("--send given but EMAIL_USERNAME / EMAIL_PASSWORD are not set; writing files only")
        send = False
    failures = skipped = 0
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
            elif send:
                try:
                    await digest.send_run(session, await session.get(digest.DigestRun, run_id), email)
                    await session.commit()
                    line += f"; emailed {email}"
                except Exception as e:  # one bad address must not stop the others
                    await session.rollback()
                    failures += 1
                    line += f"; email FAILED: {type(e).__name__}: {e}"
            print(line)
        print(f"{len(summary)} digest(s) written" + (f", {len(summary) - failures - skipped} emailed" if send else ""))
    await engine.dispose()
    return 1 if failures else 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main("--send" in sys.argv)))
