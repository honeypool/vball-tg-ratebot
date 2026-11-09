from datetime import datetime, timezone

import bot
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


def test_discipline_screen_window_and_buttons():
    data = {"scores": [], "start": 0, "header": ["Rating Anna", "Games together: 2"], "name": "Anna"}
    rich, kb = bot.discipline_screen(data)
    levels = [b.text for b in kb.inline_keyboard[0]]
    assert levels == ["0", "1", "2", "3", "4"]
    nav = [b.text for b in kb.inline_keyboard[1]]
    assert nav == ["Can't judge", "▶"]            # no ◀ at the bottom edge
    table = [b for b in rich.blocks if b.type == "table"][0]
    assert len(table.cells) == 6                   # header + 5 levels

    data = {"scores": [3] * 5, "start": 2, "header": [], "name": "yourself"}
    rich, kb = bot.discipline_screen(data)
    assert [b.text for b in kb.inline_keyboard[1]] == ["◀", "Can't judge"]
    assert DISCIPLINES[5] == "defense"
    # buttons carry the step so stale presses can be ignored
    assert bot.Score.unpack(kb.inline_keyboard[0][0].callback_data).step == 5
