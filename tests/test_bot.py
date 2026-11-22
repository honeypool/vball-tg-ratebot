from datetime import datetime, timedelta, timezone

import bot
import texts as T

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


EN = lambda k, **kw: T.t("en", k, **kw)


def buttons(kb):
    return [[(b.text, b.callback_data) for b in row] for row in kb.inline_keyboard]


def texts(kb):
    return [[t for t, _ in row] for row in buttons(kb)]


def test_rating_header_first_time_and_again():
    last = bot.iso(bot.now() - timedelta(days=13))
    first = bot.rating_header("Anna (@anna)", 1, last, None, EN, "en")
    assert first.split("\n") == [
        "You have selected <b>Anna (@anna)</b> for rating.",
        "Games together: 1, last: 13 days ago.",
        "You rate this player for the first time.",
        "*Players of both genders should be rated on games with standard male net height of 2.43m."]
    again = bot.rating_header("Anna", 2, last, bot.iso(bot.now() - timedelta(days=5)), EN, "en")
    assert "You have already rated this player 5 days ago." in again
    assert "feel free" not in first + again


def test_discipline_list_peer():
    text, kb = bot.disciplines_screen(42, "HEADER", "Anna <a>", [None, 3, 2, 3, 2, None], False, EN, "en")
    assert text == "HEADER\n\nChoose the discipline to rate/rerate for <b>Anna &lt;a&gt;</b>:"
    assert texts(kb) == [["❓Serve »"], ["✅Receive - 3"], ["✅Set - 2"], ["✅Attack - 3"],
                         ["✅Block - 2"], ["❓Defense »"], ["« Back to menu"]]
    assert buttons(kb)[1][0][1] == bot.RateOpen(pid=42, d=1).pack()
    assert buttons(kb)[6][0][1] == bot.Menu(action="home").pack()


def test_discipline_list_self_needs_one_score_for_done():
    _, kb = bot.disciplines_screen(7, "INTRO", "", [None] * 6, True, EN, "en")
    assert len(kb.inline_keyboard) == 6                                     # no Done, no menu
    ru = lambda k, **kw: T.t("ru", k, **kw)
    text, kb = bot.disciplines_screen(7, "INTRO", "", [None, 4, None, None, None, None], True, ru, "ru")
    assert texts(kb)[:2] == [["❓Подача »"], ["✅Приём - 4"]] and texts(kb)[6] == ["✅ Готово"]
    assert buttons(kb)[6][0][1] == bot.Menu(action="selfdone").pack()


def test_level_screen_unrated():
    text, kb = bot.level_screen(42, 5, "Anna (@anna)", None, False, EN, "en")
    lines = text.split("\n")
    assert lines[:2] == ["Rate <b>Anna (@anna)</b> skill:", "<b>Defense:</b>"]
    assert lines[2:] == [f"<b>•{n}•</b> {T.criteria('en', n, 'defense')}" for n in range(7)]   # all 7 levels
    assert "it's hit" in lines[2]                                       # plain text, no &#x27;
    assert texts(kb) == [["0", "1", "2", "3", "4", "5", "6"], ["Keep unrated, go back to list"]]
    assert buttons(kb)[0][3][1] == bot.RateSet(pid=42, d=5, v=3).pack()
    assert buttons(kb)[1][0][1] == bot.RateSet(pid=42, d=5, v=bot.KEEP).pack()


def test_level_screen_rated_and_self():
    text, kb = bot.level_screen(7, 0, "", 1, True, lambda k, **kw: T.t("lv", k, **kw), "lv")
    assert text.startswith("Novērtējiet savu prasmi:\n<b>Servēšana:</b>\n<b>•0•</b> ")
    assert texts(kb) == [["0", "1✓", "2", "3", "4", "5", "6"], ["Dzēst vērtējumu •1• disciplīnā Servēšana"]]
    assert buttons(kb)[1][0][1] == bot.RateSet(pid=7, d=0, v=bot.ERASE).pack()
    text, _ = bot.level_screen(7, 0, "", 1, False, EN, "en")
    assert len(text) < 4096


def game_buttons(kb):
    return [[(b.text, b.callback_data) for b in row] for row in kb.inline_keyboard]


def test_my_games_screen_lists_games_and_new_game_button():
    tr = lambda k, **kw: T.t("en", k, **kw)
    games = [{"id": 7, "start_at": "2026-11-08T17:00:00Z", "n": 2, "mine_pending": 0, "avg_level": 3.24},
             {"id": 8, "start_at": "2026-11-01T17:00:00Z", "n": 5, "mine_pending": 1, "avg_level": None}]
    text, kb = bot.games_screen(games, 0, tr, "en")
    rows = game_buttons(kb)
    assert text.startswith("Your games (page 1/1)")
    assert rows[0] == [("08.11 (Sun) 19:00 · 👤 2 · [3.2]", bot.MyGames(kind="open", value=7).pack())]
    assert rows[1][0][0] == "01.11 (Sun) 19:00 · 👤 5 ⏳"                 # no average without rated players                       # my participation not confirmed yet
    assert rows[2] == [("➕ Submit a new game", bot.Menu(action="submit").pack())]
    assert rows[3][0][0] == "« Menu"


def test_my_games_empty_and_paging():
    tr = lambda k, **kw: T.t("en", k, **kw)
    text, kb = bot.games_screen([], 0, tr, "en")
    assert text == T.t("en", "MY_GAMES_EMPTY") and len(kb.inline_keyboard) == 2
    games = [{"id": i, "start_at": "2026-11-08T17:00:00Z", "n": 4, "mine_pending": 0, "avg_level": None}
             for i in range(12)]
    text, kb = bot.games_screen(games, 1, tr, "en")
    rows = game_buttons(kb)
    assert "page 2/2" in text and len(rows) == 2 + 3        # 2 games, ◀, new game, menu
    assert rows[2] == [("◀", bot.MyGames(kind="list", value=0).pack())]


def test_admin_notice_names_both_players():
    text = bot.admin_notice_text("en", 12, "2026-11-08T17:00:00Z", "Bob @bob", 22, "Anna <a>", 11, said_no=True)
    assert "Game #12: 08.11 19:00" in text
    assert "Removed player: Bob @bob (id 22)" in text and "Added by: Anna &lt;a&gt; (id 11)" in text
    assert "answered \"I didn't play\"" in text
    text = bot.admin_notice_text("lv", 12, "2026-11-08T17:00:00Z", "Bob", 22, "Anna", None, said_no=False)
    assert "nav atbildes 48 stundu laikā" in text and "(id –)" in text


def test_game_detail_weekday_average_and_tags():
    tr = lambda k, **kw: T.t("en", k, **kw)
    g = {"start_at": "2026-11-08T17:00:00Z", "avg_level": 3.24, "rated_n": 2, "n": 3}
    people = [("Anna <a>", {"pending": 0, "tag": "[~3.5]"}), ("Bob", {"pending": 0, "tag": "[3.0]"}),
              ("Carl", {"pending": 0, "tag": None}), ("Dan", {"pending": 1, "tag": "[~~2.0]"})]
    text = bot.game_detail_text(g, people, tr, "en")
    assert text.startswith("🏐 Game on <b>08.11 (Sun) at 19:00</b>\n"
                           "Average level: <b>3.2</b>\n\n")
    assert "• Anna &lt;a&gt; [~3.5]\n• Bob [3.0]\n• Carl\n⏳ Dan [~~2.0]" in text
    ru = lambda k, **kw: T.t("ru", k, **kw)
    text = bot.game_detail_text({**g, "avg_level": None, "rated_n": 0}, people[2:3], ru, "ru")
    assert "08.11 (вс)" in text and "Средний уровень: –" in text
