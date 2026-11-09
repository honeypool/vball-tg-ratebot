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
    gid = db.create_game(start, players[0])
    for p in players[1:]:
        assert db.join_game(gid, p) == "ok"
    return gid


def test_near_game_found_within_hour(db):
    t = now() - timedelta(days=1)
    gid = db.create_game(t, 1)
    assert db.find_game_near(t + timedelta(minutes=30))["id"] == gid
    assert db.find_game_near(t + timedelta(minutes=61)) is None
    assert db.game(gid)["n"] == 1  # creator auto-joined


def test_join_rejections(db):
    t = now() - timedelta(days=1)
    g1 = db.create_game(t, 1)
    g2 = db.create_game(t + timedelta(minutes=45), 2)
    assert db.join_game(g1, 1) == "already"
    assert db.join_game(g2, 1) == "clash"          # 19:00 vs 19:45
    assert db.join_game(999, 1) == "missing"
    g3 = db.create_game(t + 3 * H, 3)
    assert db.join_game(g3, 1) == "ok"


def test_full_game(db, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "MAX_PLAYERS", 2)
    g = db.create_game(now() - H, 1)
    assert db.join_game(g, 2) == "ok"
    assert db.join_game(g, 3) == "full"


def test_three_player_game_does_not_count(db):
    game_with(db, [1, 2, 3], now() - timedelta(days=2))
    assert db.rate_candidates(1, RERATE) == []
    assert db.together(1, 2)[0] == 0
    assert db.together_matrix() == {}


def test_four_player_game_counts(db):
    gid = game_with(db, [1, 2, 3], now() - timedelta(days=2))
    db.join_game(gid, 4)
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
