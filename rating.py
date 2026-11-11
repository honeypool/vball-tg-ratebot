"""Level calculation (README §8). Pure math: no database, no Telegram."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

# --- constants (README §8) ---------------------------------------------------
SELF_WEIGHT = 0.3          # weight of a self-rating
GAMES_K = 3                # w_games = g / (g + GAMES_K)
GAP_STEP = 0.25            # w_gap drops by this per level the rater is below the player
GAP_MIN = 0.25             # ... but never below this
HALF_LIFE_DAYS = 180       # w_age halves every HALF_LIFE_DAYS
CONFIDENT_WEIGHT = 1.5     # peer_weight_sum needed to drop the "~" prefix

MIN_LEVEL, MAX_LEVEL = 0, 6
WINDOW = 5                 # levels shown at once on the rating screen

DISCIPLINES = ("serving", "serve_receive", "setting", "attacking", "blocking", "defense")


@dataclass
class Rating:
    player_id: int
    rater_id: int
    scores: tuple[int | None, ...]   # one per DISCIPLINES entry, None = "can't judge"
    updated_at: datetime


@dataclass
class Result:
    level: float                         # overall level, unrounded
    disciplines: dict[str, float]        # only disciplines that have a value
    peer_weight_sum: float
    has_peers: bool

    @property
    def tag(self) -> str:
        return tag_text(self.level, self.peer_weight_sum, self.has_peers)


def peer_weight(games: int, player_level: float | None, rater_level: float | None,
                age_days: float) -> float:
    """Weight of one peer rating. A missing level (before the first nightly run) gives no gap penalty."""
    w_games = games / (games + GAMES_K)
    gap = 0.0
    if player_level is not None and rater_level is not None:
        gap = max(0.0, player_level - rater_level)
    w_gap = max(GAP_MIN, 1 - GAP_STEP * gap)
    w_age = 0.5 ** (max(0.0, age_days) / HALF_LIFE_DAYS)
    return w_games * w_gap * w_age


def tag_text(level: float, peer_weight_sum: float, has_peers: bool) -> str:
    if not has_peers:
        prefix = "~~"
    elif peer_weight_sum < CONFIDENT_WEIGHT:
        prefix = "~"
    else:
        prefix = ""
    return f"[{prefix}{level:.1f}]"


def pair(a: int, b: int) -> tuple[int, int]:
    """Key for games_together: the two ids in ascending order."""
    return (a, b) if a < b else (b, a)


def compute_levels(ratings: list[Rating], games_together: dict[tuple[int, int], int],
                   prev_levels: dict[int, float | None], now: datetime) -> dict[int, Result]:
    """Levels for every player who has at least one usable rating.

    games_together: counting games per pair, keyed by pair(a, b).
    prev_levels: last night's stored overall levels (inputs for w_gap).
    """
    by_player: dict[int, list[tuple[float, Rating]]] = {}
    peer_sum: dict[int, float] = {}
    has_peers: set[int] = set()

    for r in ratings:
        if r.rater_id == r.player_id:
            w = SELF_WEIGHT
        else:
            age_days = (now - r.updated_at).total_seconds() / 86400
            w = peer_weight(games_together.get(pair(r.player_id, r.rater_id), 0),
                            prev_levels.get(r.player_id), prev_levels.get(r.rater_id), age_days)
            peer_sum[r.player_id] = peer_sum.get(r.player_id, 0.0) + w
            has_peers.add(r.player_id)
        by_player.setdefault(r.player_id, []).append((w, r))

    results: dict[int, Result] = {}
    for pid, weighted in by_player.items():
        disciplines: dict[str, float] = {}
        for i, name in enumerate(DISCIPLINES):
            num = den = 0.0
            for w, r in weighted:
                if r.scores[i] is not None:
                    num += w * r.scores[i]
                    den += w
            if den > 0:
                disciplines[name] = num / den
        if disciplines:
            results[pid] = Result(
                level=sum(disciplines.values()) / len(disciplines),
                disciplines=disciplines,
                peer_weight_sum=peer_sum.get(pid, 0.0),
                has_peers=pid in has_peers,
            )
    return results


# --- rating screen window (README §7) ----------------------------------------
def window_start(center: float) -> int:
    """First level of the 5-level window centered on round(center), clamped to 0..6."""
    c = math.floor(center + 0.5)
    return clamp_window(c - WINDOW // 2)


def clamp_window(start: int) -> int:
    return max(MIN_LEVEL, min(MAX_LEVEL - WINDOW + 1, start))
