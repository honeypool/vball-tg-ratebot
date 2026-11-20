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
    base = {"scores": [], "level": 0, "name": "Anna @anna", "self": False}
    return bot.discipline_screen({**base, **data}, lang)


def buttons(kb):
    return [[(b.text, bot.Score.unpack(b.callback_data)) for b in row] for row in kb.inline_keyboard]


def quoted(rich):
    return [b.text for b in rich.blocks[2].blocks]


def heading(rich):
    """Prompt line, then the discipline in bold on its own line."""
    assert rich.blocks[1].text.type == "bold"
    return rich.blocks[0].text, rich.blocks[1].text.text


def test_first_discipline_shows_only_level_0():
    rich, kb = screen()
    assert [b.type for b in rich.blocks] == ["paragraph", "paragraph", "blockquote"]
    assert heading(rich) == ("Please rate Anna (@anna) in:", "Serving (1/6)")
    assert quoted(rich) == ["Level 0:", T.criteria("en", 0, "serving")]   # one level only
    rows = buttons(kb)
    assert [t for t, _ in rows[0]] == ["Choose 0", "1▶"]                  # no ◀ at level 0
    assert rows[0][0][1].kind == "choose" and rows[0][0][1].value == 0
    assert rows[0][1][1].kind == "show" and rows[0][1][1].value == 1
    assert [t for t, _ in rows[1]] == ["Can't judge (skip)"]


def test_middle_level_buttons_show_neighbours():
    _, kb = screen(level=1)
    assert [t for t, _ in buttons(kb)[0]] == ["◀0", "Choose 1", "2▶"]


def test_top_level_and_step():
    _, kb = screen(scores=[3] * 5, level=6)
    rows = buttons(kb)
    assert [t for t, _ in rows[0]] == ["◀5", "Choose 6"]                  # no ▶ at level 6
    assert DISCIPLINES[5] == "defense"
    assert all(cd.step == 5 for row in rows for _, cd in row)            # stale presses can be ignored


def test_name_without_username():
    rich, _ = screen(name="Anna")
    assert heading(rich)[0] == "Please rate Anna in:"


def test_self_rating_prompt_and_translations():
    rich, _ = screen(lang="ru", self=True, name="", level=3)
    assert heading(rich) == ("Оцените себя в дисциплине:", "Подача (1/6)")
    assert quoted(rich)[0] == "Уровень 3:"
    rich, kb = screen(lang="lv", level=2)
    assert heading(rich)[1] == "Servēšana (1/6)" and quoted(rich)[0] == "2. līmenis:"
    assert buttons(kb)[1][0][0] == "Nevaru novērtēt (izlaist)"


def test_confirm_text_mentions_previous_rating():
    tr = lambda k, **kw: T.t("en", k, **kw)
    last = bot.iso(bot.now())
    first = bot.confirm_text("Anna", 2, last, None, tr, "en")
    assert "already rated" not in first and "Games together: 2, last: today." in first
    again = bot.confirm_text("Anna", 2, last, "2026-11-10T10:00:00Z", tr, "en")
    assert "You already rated this player on 10.11.2026." in again


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
