"""
B2B events and webhooks: record new notices and official changes as events (the /api/v1/changes feed), queue a
delivery per registered webhook, and send the deliveries that are due (retries included).

    $env:PYTHONPATH="C:\\Users\\krishna"
    python govcareeros/scripts/dispatch_webhooks.py                 # collect, queue, deliver
    python govcareeros/scripts/dispatch_webhooks.py --collect-only  # only fill the change feed

Run it after the daily job (scripts/daily_job.py) and every few minutes while retries are pending; runs may overlap
(each delivery is claimed before it is sent, and each attempt ends within 10 seconds). Deliveries go only to public
https hosts; a host that resolves to a private address is refused and never retried (backend/services/webhooks.py).
"""
import argparse
import asyncio
import sys

from govcareeros.backend.database.session import AsyncSessionLocal, engine
from govcareeros.backend.services import b2b_events, webhooks

async def main(collect_only: bool) -> int:
    try:
        return await _run(collect_only)
    finally:
        await engine.dispose()

async def _run(collect_only: bool) -> int:
    async with AsyncSessionLocal() as session:
        # Each step commits on its own: collect holds the event lock only for its own short transaction
        recorded = await b2b_events.collect(session)
        await session.commit()
        print(f"events recorded: {recorded}")
        if collect_only:
            return 0
        queued = await webhooks.enqueue(session)
        await session.commit()
        print(f"deliveries queued: {queued}")
        sent = await webhooks.deliver_due(session)  # commits each delivery's claim and outcome itself
        print(f"deliveries: {sent}")
    return 0

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--collect-only", action="store_true", help="record events only; send nothing")
    sys.exit(asyncio.run(main(p.parse_args().collect_only)))
