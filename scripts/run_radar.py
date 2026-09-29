"""
Daily job: refresh official data, then run the eligibility radar for every user.

    $env:PYTHONPATH="C:\\Users\\krishna"
    python govcareeros/scripts/run_radar.py            # ingest UPSC (ads + notices), then radar
    python govcareeros/scripts/run_radar.py --no-fetch # radar only
"""
import asyncio
import sys

from sqlalchemy import func, select

from govcareeros.backend.database.session import AsyncSessionLocal, engine
from govcareeros.backend.models import UserAlert
from govcareeros.backend.services.document_ingestion import run_upsc_pipeline
from govcareeros.backend.services.radar import run_radar

async def main(fetch: bool) -> int:
    async with AsyncSessionLocal() as session:
        if fetch:
            pipeline = await run_upsc_pipeline(session)
            await session.commit()
            print(f"UPSC: {pipeline.source_health}; advertisements {len(pipeline.documents)}, notices {len(pipeline.notices)}; errors {pipeline.errors}")
        run = await run_radar(session)
        await session.commit()
        print(f"Radar: {run.users} user(s), {run.evaluations} new evaluation(s), alerts created {run.alerts_created or '{}'}")
        unread = (await session.execute(select(UserAlert.kind, func.count()).where(UserAlert.read_at.is_(None)).group_by(UserAlert.kind))).all()
        print("Unread alerts by kind:", dict(unread))
    await engine.dispose()
    return 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main("--no-fetch" not in sys.argv)))
