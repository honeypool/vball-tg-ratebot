"""Nightly level recalculation, group tag updates and the CSV export.

Run once by hand:  python nightly.py --once
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import zipfile
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter

from db import DB, TABLES, now
from rating import Result, compute_levels

log = logging.getLogger("nightly")

TAG_CALLS_PER_SECOND = 20


async def set_tag(bot: Bot, group_id: int, uid: int, tag: str) -> bool:
    """Set (or with "" remove) a member tag. Failures are logged and skipped (README §2)."""
    for _ in range(3):
        try:
            await bot.set_chat_member_tag(chat_id=group_id, user_id=uid, tag=tag)
            return True
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except TelegramAPIError as e:
            log.warning("setChatMemberTag(%s, %r) failed: %s", uid, tag, e)
            return False
    return False


def levels(db: DB) -> dict[int, Result]:
    """Current levels computed live from the database, using last night's stored levels as input."""
    prev = {p["telegram_id"]: p["level"] for p in db.all_players()}
    return compute_levels(db.ratings(), db.together_matrix(), prev, now())


async def run(bot: Bot, db: DB, group_id: int) -> None:
    results = levels(db)
    players = db.all_players()
    with db.tx():
        for p in players:
            r = results.get(p["telegram_id"])
            db.set_level(p["telegram_id"], r.level if r else None)

    changed = 0
    for p in players:
        r = results.get(p["telegram_id"])
        if r is None or r.tag == p["tag_shown"]:
            continue
        if await set_tag(bot, group_id, p["telegram_id"], r.tag):
            db.set_tag_shown(p["telegram_id"], r.tag)
            changed += 1
        await asyncio.sleep(1 / TAG_CALLS_PER_SECOND)
    log.info("nightly: %d players, %d with a level, %d tags updated", len(players), len(results), changed)


def seconds_until_midnight_utc() -> float:
    t = datetime.now(timezone.utc)
    nxt = (t + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (nxt - t).total_seconds()


async def loop(bot: Bot, db: DB, group_id: int) -> None:
    while True:
        await asyncio.sleep(seconds_until_midnight_utc() + 1)
        try:
            await run(bot, db, group_id)
        except Exception:
            log.exception("nightly run failed")


def export_zip(db: DB) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for table in TABLES:
            cols, rows = db.dump(table)
            s = io.StringIO()
            w = csv.writer(s)
            w.writerow(cols)
            w.writerows(rows)
            z.writestr(f"{table}.csv", s.getvalue())
    return buf.getvalue()


if __name__ == "__main__":
    import sys

    from bot import ADMIN_ID, BOT_TOKEN, DB_PATH, GROUP_ID  # noqa: F401  (config lives in bot.py)

    if "--once" not in sys.argv:
        sys.exit("usage: python nightly.py --once")

    async def main():
        logging.basicConfig(level=logging.INFO)
        async with Bot(BOT_TOKEN) as bot:
            await run(bot, DB(DB_PATH), GROUP_ID)

    asyncio.run(main())
