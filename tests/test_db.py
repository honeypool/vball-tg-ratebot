from datetime import timedelta
import sqlite3

import pytest

from db import DB, now, parse

H = timedelta(hours=1)
RERATE = timedelta(days=90)


@pytest.fixture
def db():
    d = DB(":memory:")
    for uid in range(1, 8):
        d.add_player(uid)
    return d


def game_with(db, players, start, confirm=True):
    gid, res = db.create_game(start, players[0], players)
    assert res.added == players
    if confirm:
        for p in players:
            db.confirm(gid, p)
    return gid


def test_create_game_with_selected_players(db):
    gid = game_with(db, [1, 2, 3, 4], now() - H)
    assert sorted(db.game_player_ids(gid)) == [1, 2, 3, 4]
    assert db.game(gid)["created_by"] == 1


def test_matching_game_needs_time_and_shared_player(db):
    t = now() - timedelta(days=1)
    gid = game_with(db, [1, 2, 3, 4], t)
    half = timedelta(minutes=30)
    assert db.matching_game(t + half, [4, 5, 6, 7])["id"] == gid      # 30 min away, shares player 4
    assert db.matching_game(t + half, [5, 6, 7]) is None             # same time, other court
    assert db.matching_game(t + timedelta(minutes=61), [1, 2]) is None
    assert db.one("SELECT COUNT(*) FROM games") == 1                  # matching creates nothing


def test_matching_prefers_most_shared(db):
    t = now() - timedelta(days=1)
    game_with(db, [1, 2, 3, 4], t)
    g2 = game_with(db, [5, 6, 7], t + timedelta(minutes=10))
    assert db.matching_game(t, [1, 5, 6])["id"] == g2


def test_add_players_skips_existing_and_clashes(db):
    t = now() - timedelta(days=1)
    g1 = game_with(db, [1, 2, 3, 4], t)
    game_with(db, [5, 6], t + timedelta(minutes=45))                  # 19:00 vs 19:45
    res = db.add_players(g1, [1, 5, 7], 1)
    assert res.added == [7] and res.clash == [5] and res.full == []
    assert sorted(db.game_player_ids(g1)) == [1, 2, 3, 4, 7]


def test_full_game(db, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "MAX_PLAYERS", 2)
    gid, res = db.create_game(now() - H, 1, [1, 2, 3])
    assert res.added == [1, 2] and res.full == [3]


def test_three_player_game_does_not_count(db):
    game_with(db, [1, 2, 3], now() - timedelta(days=2))
    assert db.rate_candidates(1, RERATE) == []
    assert db.together(1, 2)[0] == 0
    assert db.together_matrix() == {}


def test_four_player_game_counts(db):
    gid = game_with(db, [1, 2, 3], now() - timedelta(days=2))
    db.add_players(gid, [4], 1)
    db.confirm(gid, 4)
    assert {r["pid"] for r in db.rate_candidates(1, RERATE)} == {2, 3, 4}
    assert db.together(1, 2)[0] == 1
    assert db.together_matrix()[(1, 2)] == 1
    assert len(db.together_matrix()) == 6


def test_candidate_order(db):
    t = now() - timedelta(days=10)
    game_with(db, [1, 2, 3, 4], t)
    game_with(db, [1, 2, 5, 6], t + 3 * H)
    # 1 already rated 3 (recently) -> last; 4 has a peer rating from 5 -> after 2/5/6
    db.save_rating(3, 1, [3] * 6)
    db.save_rating(4, 5, [3] * 6)
    order = [r["pid"] for r in db.rate_candidates(1, RERATE)]
    assert order[0] == 2            # needs rating, 0 peer ratings, 2 games together
    assert set(order[1:3]) == {5, 6}
    assert order[3:] == [4, 3]


def test_rating_overwrite(db):
    assert db.save_rating(2, 1, [1, 1, 1, 1, 1, 1])
    first = db.one("SELECT updated_at FROM ratings")
    db.conn.execute("UPDATE ratings SET updated_at = '2020-01-01T00:00:00Z'")
    assert db.save_rating(2, 1, [5, None, 5, 5, 5, 5])
    rows = db.q("SELECT * FROM ratings")
    assert len(rows) == 1
    assert rows[0]["serving"] == 5 and rows[0]["serve_receive"] is None
    assert rows[0]["updated_at"] >= first
    assert parse(rows[0]["created_at"]) <= parse(rows[0]["updated_at"])


def test_all_cant_judge_saves_nothing(db):
    assert db.save_rating(2, 1, [None] * 6) is False
    assert db.one("SELECT COUNT(*) FROM ratings") == 0


def test_delete_user(db):
    g = game_with(db, [1, 2, 3, 4], now() - H * 5)
    db.save_rating(1, 1, [3] * 6)
    db.save_rating(2, 1, [3] * 6)
    db.save_rating(1, 2, [3] * 6)
    db.save_rating(3, 2, [3] * 6)
    assert db.delete_user(1)
    assert db.player(1) is None
    assert db.one("SELECT COUNT(*) FROM ratings") == 1          # only 2 -> 3 left
    assert db.one("SELECT COUNT(*) FROM game_players WHERE player_id = 1") == 0
    assert db.game(g)["n"] == 3                                 # game stays
    assert db.delete_user(1) is False


def test_peer_and_given_counts(db):
    db.save_rating(1, 1, [3] * 6)
    db.save_rating(1, 2, [3] * 6)
    db.save_rating(1, 3, [3] * 6)
    db.save_rating(2, 1, [3] * 6)
    assert db.peer_count(1) == 2
    assert db.given_count(1) == 1
    assert db.is_self_rated(1) and not db.is_self_rated(2)


def test_recently_rated_flag_and_date(db):
    game_with(db, [1, 2, 3, 4], now() - timedelta(days=2))
    db.save_rating(2, 1, [3] * 6)
    rows = {r["pid"]: r for r in db.rate_candidates(1, RERATE)}
    assert rows[2]["needs"] == 0 and rows[3]["needs"] == 1
    assert [r["pid"] for r in db.rate_candidates(1, RERATE)][-1] == 2
    assert db.rated_at(2, 1) == rows[2]["mine"]
    assert db.rated_at(3, 1) is None
    db.conn.execute("UPDATE ratings SET updated_at = '2020-01-01T00:00:00Z'")   # stale -> needs again
    assert {r["pid"]: r["needs"] for r in db.rate_candidates(1, RERATE)}[2] == 1


def test_my_games_all_newest_first(db):
    old = game_with(db, [1, 2], now() - timedelta(days=30))          # 2 players can be submitted
    new = game_with(db, [1, 3, 4, 5], now() - timedelta(days=1))
    game_with(db, [6, 7], now() - timedelta(days=2))                  # not mine
    rows = db.recent_games(1, limit=-1)
    assert [(r["id"], r["n"]) for r in rows] == [(new, 4), (old, 2)]
    assert db.rate_candidates(1, RERATE) and 2 not in {r["pid"] for r in db.rate_candidates(1, RERATE)}


def test_added_players_pending_until_confirmed(db):
    gid, res = db.create_game(now() - timedelta(days=1), 1, [1, 2, 3, 4])
    assert db.pending_ids(gid) == {2, 3, 4}                            # the submitter is confirmed at once
    assert db.game(gid)["n"] == 1 and db.game(gid)["pending"] == 3
    assert db.together(1, 2)[0] == 0 and db.rate_candidates(1, RERATE) == [] and db.together_matrix() == {}
    for p in (2, 3, 4):
        assert db.confirm(gid, p)
    assert not db.confirm(gid, 2)                                       # only once
    assert db.together(1, 2)[0] == 1 and len(db.together_matrix()) == 6
    assert db.recent_games(2, limit=-1)[0]["mine_pending"] == 0


def test_remove_pending_and_expiry(db):
    gid, _ = db.create_game(now() - timedelta(days=3), 1, [1, 2, 3])
    db.add_players(gid, [4], 2)
    row = db.remove_pending(gid, 2)
    assert row["added_by"] == 1 and db.remove_pending(gid, 2) is None   # second click does nothing
    assert db.remove_pending(gid, 1) is None                            # confirmed rows are never removed
    db.conn.execute("UPDATE game_players SET joined_at = '2020-01-01T00:00:00Z' WHERE player_id = 3")
    assert [r["player_id"] for r in db.expired_pending(now() - timedelta(hours=48))] == [3]
    assert db.expired_pending(now() - timedelta(hours=48))[0]["added_by"] == 1
    assert {r["player_id"]: r["added_by"] for r in db.q("SELECT * FROM game_players")}[4] == 2


def test_delete_user_clears_added_by(db):
    gid, _ = db.create_game(now() - H, 1, [1, 2])
    db.delete_user(1)
    assert db.q("SELECT player_id, added_by FROM game_players")[0][:] == (2, None)


def test_migration_marks_old_participation_confirmed(tmp_path):
    path = str(tmp_path / "old.sqlite")
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE players (telegram_id INTEGER PRIMARY KEY, consent_at TEXT NOT NULL, level REAL, tag_shown TEXT);
        CREATE TABLE games (id INTEGER PRIMARY KEY, start_at TEXT NOT NULL, created_by INTEGER NOT NULL,
                            created_at TEXT NOT NULL);
        CREATE TABLE game_players (game_id INTEGER NOT NULL, player_id INTEGER NOT NULL, joined_at TEXT NOT NULL,
                                   PRIMARY KEY (game_id, player_id));
        INSERT INTO players VALUES (1, '2026-01-01T00:00:00Z', NULL, NULL), (2, '2026-01-01T00:00:00Z', NULL, NULL),
                                   (3, '2026-01-01T00:00:00Z', NULL, NULL), (4, '2026-01-01T00:00:00Z', NULL, NULL);
        INSERT INTO games VALUES (1, '2026-11-01T17:00:00Z', 1, '2026-11-01T20:00:00Z');
        INSERT INTO game_players VALUES (1, 1, 'x'), (1, 2, 'x'), (1, 3, 'x'), (1, 4, 'x');
    """)
    c.commit()
    c.close()
    d = DB(path)
    assert d.pending_ids(1) == set() and d.together(1, 2)[0] == 1
    assert d.game(1)["avg_level"] is None and d.player(1)["tag"] is None


def test_game_average_level_follows_levels_and_roster(db):
    gid = game_with(db, [1, 2, 3], now() - H)
    assert db.game(gid)["avg_level"] is None and db.game(gid)["rated_n"] == 0
    db.set_level(1, 2.0, "[~2.0]")
    db.set_level(2, 4.0, "[4.0]")
    db.refresh_avg()                                   # nightly
    g = db.game(gid)
    assert g["avg_level"] == 3.0 and g["rated_n"] == 2 and g["n"] == 3
    assert db.recent_games(1)[0]["avg_level"] == 3.0
    assert {r["player_id"]: r["tag"] for r in db.game_players(gid)} == {1: "[~2.0]", 2: "[4.0]", 3: None}

    db.set_level(4, 6.0, "[6.0]")
    db.add_players(gid, [4], 1)                        # pending: not in the average yet
    assert db.game(gid)["avg_level"] == 3.0
    db.confirm(gid, 4)
    assert db.game(gid)["avg_level"] == 4.0 and db.game(gid)["rated_n"] == 3
    db.delete_user(2)
    assert db.game(gid)["avg_level"] == 4.0 and db.game(gid)["rated_n"] == 2      # (2 + 6) / 2


def test_set_score_one_discipline_at_a_time(db):
    db.set_score(2, 1, "defense", 3)
    r = db.rating(2, 1)
    assert r["defense"] == 3 and r["serving"] is None and r["created_at"] == r["updated_at"]
    db.set_score(2, 1, "serving", 5)
    db.set_score(2, 1, "defense", 4)
    r = db.rating(2, 1)
    assert (r["serving"], r["defense"]) == (5, 4)
    db.set_score(2, 1, "serving", None)                 # erase one
    assert db.rating(2, 1)["serving"] is None and db.rating(2, 1)["defense"] == 4
    db.set_score(2, 1, "defense", None)                 # erasing the last one deletes the rating
    assert db.rating(2, 1) is None
    db.set_score(2, 1, "blocking", None)                # nothing to erase: no-op
    assert db.rating(2, 1) is None
    with pytest.raises(ValueError):
        db.set_score(2, 1, "serving; DROP TABLE ratings", 1)


def test_partly_rated_player_still_needs_my_rating(db):
    game_with(db, [1, 2, 3, 4], now() - H)
    db.set_score(2, 1, "serving", 3)                    # only 1 of 6 disciplines
    needs = lambda: {r["pid"]: r["needs"] for r in db.rate_candidates(1, RERATE)}[2]
    assert needs() == 1
    for d in ("serve_receive", "setting", "attacking", "blocking"):
        db.set_score(2, 1, d, 3)
    assert needs() == 1                                 # 5 of 6
    db.set_score(2, 1, "defense", 2)
    assert needs() == 0                                 # all rated
