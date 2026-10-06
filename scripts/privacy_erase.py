"""
DPDP erasure, admin/cron side (backend/privacy/rights.py). Run daily: hard-erases every account whose grace period
after POST /privacy/erase has ended, except accounts under a legal hold.

    $env:PYTHONPATH="C:\\Users\\krishna"
    python govcareeros/scripts/privacy_erase.py run [--dry-run]       # erase every due account
    python govcareeros/scripts/privacy_erase.py hold <email|user_id> "<reason>"   # legal hold: never erased while set
    python govcareeros/scripts/privacy_erase.py release <email|user_id>           # lift the hold
    python govcareeros/scripts/privacy_erase.py cancel <email|user_id>            # person asked to keep the account (grace period only)

Prints user ids only, never names or emails.
"""
import argparse
import asyncio
import sys
import uuid

from sqlalchemy import select

from govcareeros.backend.database.session import AsyncSessionLocal, engine
from govcareeros.backend.models.user import User
from govcareeros.backend.privacy import rights

async def _find(session, who: str) -> User:
    try:
        user = await session.get(User, uuid.UUID(who))
    except ValueError:
        user = (await session.execute(select(User).where(User.email == who.strip().lower()))).scalar_one_or_none()
    if user is None:
        raise SystemExit("no such user")
    return user

async def main(args) -> int:
    async with AsyncSessionLocal() as session:
        if args.cmd == "run":
            report = await rights.erase_due(session, dry_run=args.dry_run)
            print(("Would erase" if args.dry_run else "Erased"), len(report["erased"]), "account(s):", *report["erased"])
            if report["held"]:
                print("Skipped under legal hold:", *report["held"])
        else:
            user = await _find(session, args.who)
            if args.cmd == "hold":
                user.legal_hold_reason = args.reason
            elif args.cmd == "release":
                user.legal_hold_reason = None
            else:
                await rights.cancel_erasure(session, user)
            print(f"{args.cmd}: {user.id}")
        if not getattr(args, "dry_run", False):
            await session.commit()
    await engine.dispose()
    return 0

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--dry-run", action="store_true")
    hold = sub.add_parser("hold")
    hold.add_argument("who")
    hold.add_argument("reason")
    for name in ("release", "cancel"):
        sub.add_parser(name).add_argument("who")
    sys.exit(asyncio.run(main(p.parse_args())))
