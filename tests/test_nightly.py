import asyncio
import io
import zipfile

import nightly
from db import DB


class FakeBot:
    def __init__(self):
        self.calls = []

    async def set_chat_member_tag(self, chat_id, user_id, tag):
        self.calls.append((user_id, tag))
        return True


def test_nightly_updates_only_changed_tags(monkeypatch):
    monkeypatch.setattr(nightly, "TAG_CALLS_PER_SECOND", 10 ** 6)
    db = DB(":memory:")
    for uid in (1, 2, 3):
        db.add_player(uid)
    db.save_rating(1, 1, [2] * 6)
    db.save_rating(2, 2, [4] * 6)
    db.set_tag_shown(1, "~~2.0")                 # already correct -> no call
    bot = FakeBot()
    asyncio.run(nightly.run(bot, db, -100))
    assert bot.calls == [(2, "~~4.0")]           # player 3 has no rating -> no tag
    assert db.player(2)["tag_shown"] == "~~4.0"
    assert db.player(1)["level"] == 2.0 and db.player(3)["level"] is None

    bot.calls.clear()
    asyncio.run(nightly.run(bot, db, -100))
    assert bot.calls == []


def test_export_zip_has_one_csv_per_table():
    db = DB(":memory:")
    db.add_player(1)
    z = zipfile.ZipFile(io.BytesIO(nightly.export_zip(db)))
    assert sorted(z.namelist()) == ["game_players.csv", "games.csv", "players.csv", "ratings.csv"]
    assert z.read("players.csv").decode().startswith("telegram_id,consent_at,level,tag_shown")
