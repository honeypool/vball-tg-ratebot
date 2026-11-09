from datetime import datetime, timedelta, timezone

import pytest

from rating import Rating, compute_levels, pair, peer_weight, tag_text, window_start, clamp_window

NOW = datetime(2026, 11, 8, tzinfo=timezone.utc)


def test_worked_example_weight():
    # rater 2.0, player 3.5, 2 games together, rating 90 days old -> 0.177
    w = peer_weight(2, 3.5, 2.0, 90)
    assert w == pytest.approx(0.4 * 0.625 * 0.5 ** 0.5)
    assert round(w, 3) == 0.177


def test_gap_only_when_rater_weaker_and_floor():
    assert peer_weight(3, 2.0, 5.0, 0) == pytest.approx(0.5)      # stronger rater: no penalty
    assert peer_weight(3, 6.0, 0.0, 0) == pytest.approx(0.5 * 0.25)  # floored at 0.25
    assert peer_weight(3, None, 1.0, 0) == pytest.approx(0.5)      # no level yet: no penalty


def test_worked_example_anna():
    # Weights 1.0 and 0.5 are given directly in the README. Reproduce them via
    # many games (w_games -> ~1) and age: simpler to monkey-patch weights by
    # constructing ratings whose computed weights are exactly 1.0 and 0.5.
    anna, r1, r2 = 1, 2, 3
    ratings = [
        Rating(anna, r1, (3, 3, 2, 3, None, 3), NOW),
        Rating(anna, r2, (2, 3, 2, 2, 2, 2), NOW - timedelta(days=180)),
    ]
    # games -> w_games: use a huge game count so w_games ~= 1
    big = 10 ** 9
    res = compute_levels(ratings, {pair(anna, r1): big, pair(anna, r2): big}, {}, NOW)[anna]
    d = res.disciplines
    assert d["serving"] == pytest.approx(8 / 3, abs=1e-6)
    assert d["serve_receive"] == pytest.approx(3.0)
    assert d["setting"] == pytest.approx(2.0)
    assert d["attacking"] == pytest.approx(8 / 3, abs=1e-6)
    assert d["blocking"] == pytest.approx(2.0)
    assert d["defense"] == pytest.approx(8 / 3, abs=1e-6)
    assert res.level == pytest.approx(2.5, abs=1e-6)
    assert res.peer_weight_sum == pytest.approx(1.5, abs=1e-6)
    # 1.5 - tiny epsilon from w_games < 1 would give "~"; README treats it as exactly 1.5
    assert tag_text(2.5, 1.5, True) == "2.5"


def test_self_only_gets_double_tilde():
    me = 7
    res = compute_levels([Rating(me, me, (2, 3, None, None, None, None), NOW)], {}, {}, NOW)[me]
    assert res.level == pytest.approx(2.5)
    assert res.tag == "~~2.5"
    assert res.peer_weight_sum == 0


def test_low_peer_weight_single_tilde():
    res = compute_levels([
        Rating(1, 1, (3,) * 6, NOW),
        Rating(1, 2, (3,) * 6, NOW),
    ], {pair(1, 2): 1}, {}, NOW)[1]
    assert res.tag == "~3.0"


def test_discipline_without_scores_is_left_out():
    res = compute_levels([Rating(1, 1, (4, None, None, None, None, 2), NOW)], {}, {}, NOW)[1]
    assert set(res.disciplines) == {"serving", "defense"}
    assert res.level == pytest.approx(3.0)


def test_zero_weight_peer_rating_ignored():
    # no counting game together any more -> w = 0, must not crash or change the level
    res = compute_levels([
        Rating(1, 1, (2,) * 6, NOW),
        Rating(1, 2, (6,) * 6, NOW),
    ], {}, {}, NOW)[1]
    assert res.level == pytest.approx(2.0)
    assert res.tag == "~2.0"


@pytest.mark.parametrize("center,start", [(0.4, 0), (3.2, 1), (5.8, 2), (3, 1), (2.5, 1), (6, 2), (0, 0)])
def test_window(center, start):
    assert window_start(center) == start


def test_window_shift_clamped():
    assert clamp_window(-1) == 0
    assert clamp_window(3) == 2
    assert clamp_window(1) == 1
