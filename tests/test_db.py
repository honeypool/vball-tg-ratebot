from datetime import timedelta

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


def game_with(db, players, start):
    gid, res = db.create_game(start, players[0], players)
    assert res.added == players
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
    res = db.add_players(g1, [1, 5, 7])
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
    db.add_players(gid, [4])
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
