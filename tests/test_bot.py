from datetime import datetime, timezone

import bot
import texts as T
from rating import DISCIPLINES

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)   # 14:00 in Riga


def test_parse_with_and_without_year():
    assert bot.parse_game_time("07.11 19:00", NOW) == datetime(2026, 11, 7, 17, 0, tzinfo=timezone.utc)
    assert bot.parse_game_time("07.11.2026 19:00", NOW) == datetime(2026, 11, 7, 17, 0, tzinfo=timezone.utc)
    # summer time (UTC+3)
    assert bot.parse_game_time("01.07 19:00", NOW) == datetime(2026, 7, 1, 16, 0, tzinfo=timezone.utc)


def test_no_year_future_date_means_last_year():
    assert bot.parse_game_time("20.12 19:00", NOW).year == 2025


def test_explicit_future_stays_future():
    assert bot.parse_game_time("20.12.2026 19:00", NOW) > NOW


def test_bad_input():
    for s in ["", "19:00", "32.11 19:00", "08.13 19:00", "08.11 25:00", "hello"]:
        assert bot.parse_game_time(s, NOW) is None


def screen(lang="en", **data):
    base = {"scores": [], "level": 0, "header": ["Rating Anna", "Games together: 2"], "name": "Anna",
            "self": False}
    return bot.discipline_screen({**base, **data}, lang)


def buttons(kb):
    return [[(b.text, bot.Score.unpack(b.callback_data)) for b in row] for row in kb.inline_keyboard]


def test_first_discipline_shows_only_level_0():
    rich, kb = screen()
    texts = [b.text for b in rich.blocks]
    assert "Serving (1/6)" in texts and "Level 0" in texts
    assert T.criteria("en", 0, "serving") in texts
    assert not any(T.criteria("en", 1, "serving") == t for t in texts)   # one level only
    assert not any(b.type == "table" for b in rich.blocks)
    assert rich.blocks[-1].text.startswith("Players of both genders")
    rows = buttons(kb)
    assert [t for t, _ in rows[0]] == ["✔ Choose 0", "▶"]                # no ◀ at level 0
    assert rows[0][0][1].kind == "choose" and rows[0][0][1].value == 0
    assert rows[0][1][1].kind == "show" and rows[0][1][1].value == 1
    assert [t for t, _ in rows[1]] == ["Can't judge"]


def test_top_level_and_step():
    _, kb = screen(scores=[3] * 5, level=6)
    rows = buttons(kb)
    assert [t for t, _ in rows[0]] == ["◀", "✔ Choose 6"]                 # no ▶ at level 6
    assert DISCIPLINES[5] == "defense"
    assert all(cd.step == 5 for row in rows for _, cd in row)            # stale presses can be ignored


def test_self_rating_question_and_translations():
    rich, _ = screen(lang="ru", self=True, header=[], level=3)
    texts = [b.text for b in rich.blocks]
    assert "Подача (1/6)" in texts and "Уровень 3" in texts
    assert "Какой уровень лучше всего подходит вам?" in texts
    assert rich.blocks[-1].text.startswith("Игроков обоих полов")
    rich, kb = screen(lang="lv", level=2)
    assert "Servēšana (1/6)" in [b.text for b in rich.blocks]
    assert buttons(kb)[1][0][0] == "Nevaru novērtēt"
