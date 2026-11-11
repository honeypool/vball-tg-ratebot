"""SQLite schema and every SQL query the bot uses (README §13).

All times are stored as UTC ISO strings in one fixed format (see `iso`), so string
comparison in SQL equals time comparison.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from rating import DISCIPLINES, Rating

MIN_COUNTING_PLAYERS = 4   # a game "counts" only with at least this many players
MAX_PLAYERS = 100          # per game
CLASH = timedelta(hours=1)  # games closer than this are "the same time"

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
  telegram_id  INTEGER PRIMARY KEY,
  consent_at   TEXT NOT NULL,
  level        REAL,
  tag_shown    TEXT
);
CREATE TABLE IF NOT EXISTS games (
  id           INTEGER PRIMARY KEY,
  start_at     TEXT NOT NULL,
  created_by   INTEGER NOT NULL,
  created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS game_players (
  game_id      INTEGER NOT NULL REFERENCES games(id),
  player_id    INTEGER NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
  joined_at    TEXT NOT NULL,
  PRIMARY KEY (game_id, player_id)
);
CREATE TABLE IF NOT EXISTS ratings (
  player_id     INTEGER NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
  rater_id      INTEGER NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
  serving       INTEGER,
  serve_receive INTEGER,
  setting       INTEGER,
  attacking     INTEGER,
  blocking      INTEGER,
  defense       INTEGER,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  PRIMARY KEY (player_id, rater_id),
  CHECK (COALESCE(serving, serve_receive, setting, attacking, blocking, defense) IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS games_start ON games(start_at);
CREATE INDEX IF NOT EXISTS gp_player ON game_players(player_id);
CREATE INDEX IF NOT EXISTS ratings_rater ON ratings(rater_id);
"""

# Games with enough players to count, reused by several queries.
COUNTING = f"""counting AS (
  SELECT game_id FROM game_players GROUP BY game_id HAVING COUNT(*) >= {MIN_COUNTING_PLAYERS}
)"""

TABLES = ("players", "games", "game_players", "ratings")


class Added(NamedTuple):
    added: list[int]   # newly added to the game
    clash: list[int]   # skipped: already in another game within ±1h
    full: list[int]    # skipped: game reached MAX_PLAYERS


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


class DB:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, isolation_level=None)  # autocommit; explicit transactions below
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)

    def q(self, sql: str, *args) -> list[sqlite3.Row]:
        return self.conn.execute(sql, args).fetchall()

    def one(self, sql: str, *args):
        row = self.conn.execute(sql, args).fetchone()
        return None if row is None else (row[0] if len(row) == 1 else row)

    def tx(self):
        """`with db.tx():` runs the block in one transaction."""
        return _Tx(self.conn)

    # --- players -------------------------------------------------------------
    def add_player(self, uid: int) -> None:
        self.conn.execute("INSERT OR IGNORE INTO players (telegram_id, consent_at) VALUES (?, ?)",
                          (uid, iso(now())))

    def player(self, uid: int) -> sqlite3.Row | None:
        rows = self.q("SELECT * FROM players WHERE telegram_id = ?", uid)
        return rows[0] if rows else None

    def is_self_rated(self, uid: int) -> bool:
        return self.one("SELECT 1 FROM ratings WHERE player_id = ? AND rater_id = ?", uid, uid) is not None

    def all_players(self) -> list[sqlite3.Row]:
        return self.q("SELECT * FROM players")

    def set_level(self, uid: int, level: float | None) -> None:
        self.conn.execute("UPDATE players SET level = ? WHERE telegram_id = ?", (level, uid))

    def set_tag_shown(self, uid: int, tag: str | None) -> None:
        self.conn.execute("UPDATE players SET tag_shown = ? WHERE telegram_id = ?", (tag, uid))

    def delete_user(self, uid: int) -> bool:
        """Revoke consent / left the group (README §11). Games stay. Returns True if the user existed."""
        with self.tx():
            self.conn.execute("DELETE FROM ratings WHERE player_id = ? OR rater_id = ?", (uid, uid))
            self.conn.execute("DELETE FROM game_players WHERE player_id = ?", (uid,))
            return self.conn.execute("DELETE FROM players WHERE telegram_id = ?", (uid,)).rowcount > 0

    # --- games ---------------------------------------------------------------
    def game(self, game_id: int) -> sqlite3.Row | None:
        rows = self.q("""SELECT g.*, (SELECT COUNT(*) FROM game_players WHERE game_id = g.id) AS n
                         FROM games g WHERE id = ?""", game_id)
        return rows[0] if rows else None

    def game_player_ids(self, game_id: int) -> list[int]:
        return [r[0] for r in self.q("SELECT player_id FROM game_players WHERE game_id = ?", game_id)]

    def matching_game(self, start: datetime, uids: list[int]) -> sqlite3.Row | None:
        """A game within ±1h of `start` sharing at least one of `uids` (the one sharing most)."""
        marks = ",".join("?" * len(uids))
        rows = self.q(f"""SELECT g.*, COUNT(*) AS shared FROM games g
                          JOIN game_players gp ON gp.game_id = g.id AND gp.player_id IN ({marks})
                          WHERE g.start_at BETWEEN ? AND ?
                          GROUP BY g.id ORDER BY shared DESC, g.start_at LIMIT 1""",
                      *uids, iso(start - CLASH), iso(start + CLASH))
        return rows[0] if rows else None

    def create_game(self, start: datetime, creator: int, uids: list[int]) -> tuple[int, Added]:
        """New game with the selected players (README §6)."""
        with self.tx():
            gid = self.conn.execute("INSERT INTO games (start_at, created_by, created_at) VALUES (?, ?, ?)",
                                    (iso(start), creator, iso(now()))).lastrowid
            return gid, self._add(gid, start, uids)

    def add_players(self, game_id: int, uids: list[int]) -> Added:
        with self.tx():
            return self._add(game_id, parse(self.game(game_id)["start_at"]), uids)

    def _add(self, game_id: int, start: datetime, uids: list[int]) -> Added:
        """Adds players who are not in it yet. Skips anyone in another game within ±1h, and stops at
        MAX_PLAYERS. Call inside a transaction."""
        res = Added([], [], [])
        n = self.one("SELECT COUNT(*) FROM game_players WHERE game_id = ?", game_id)
        t = iso(now())
        for uid in uids:
            if self.one("SELECT 1 FROM game_players WHERE game_id = ? AND player_id = ?", game_id, uid):
                continue
            if self.one("""SELECT 1 FROM game_players gp JOIN games g ON g.id = gp.game_id
                           WHERE gp.player_id = ? AND g.id != ? AND g.start_at BETWEEN ? AND ?""",
                        uid, game_id, iso(start - CLASH), iso(start + CLASH)):
                res.clash.append(uid)
            elif n >= MAX_PLAYERS:
                res.full.append(uid)
            else:
                self.conn.execute("INSERT INTO game_players (game_id, player_id, joined_at) VALUES (?, ?, ?)",
                                  (game_id, uid, t))
                res.added.append(uid)
                n += 1
        return res

    def recent_games(self, uid: int, limit: int = 10) -> list[sqlite3.Row]:
        return self.q("""SELECT g.id, g.start_at, (SELECT COUNT(*) FROM game_players WHERE game_id = g.id) AS n
                         FROM games g JOIN game_players gp ON gp.game_id = g.id
                         WHERE gp.player_id = ? ORDER BY g.start_at DESC LIMIT ?""", uid, limit)

    # --- games together --------------------------------------------------------
    def together(self, a: int, b: int) -> tuple[int, str | None]:
        """(counting games a and b played together, start_at of the last one)."""
        row = self.q(f"""WITH {COUNTING}
            SELECT COUNT(*), MAX(g.start_at) FROM game_players x
            JOIN game_players y ON y.game_id = x.game_id
            JOIN counting c ON c.game_id = x.game_id
            JOIN games g ON g.id = x.game_id
            WHERE x.player_id = ? AND y.player_id = ?""", a, b)[0]
        return row[0], row[1]

    def together_matrix(self) -> dict[tuple[int, int], int]:
        """Counting games for every pair, keyed (smaller id, larger id)."""
        rows = self.q(f"""WITH {COUNTING}
            SELECT x.player_id, y.player_id, COUNT(*) FROM game_players x
            JOIN game_players y ON y.game_id = x.game_id AND y.player_id > x.player_id
            JOIN counting c ON c.game_id = x.game_id
            GROUP BY x.player_id, y.player_id""")
        return {(r[0], r[1]): r[2] for r in rows}

    def rate_candidates(self, rater: int, rerate_after: timedelta) -> list[sqlite3.Row]:
        """Players the rater may rate, sorted per README §7."""
        stale = iso(now() - rerate_after)
        return self.q(f"""WITH {COUNTING},
            mates AS (
              SELECT y.player_id AS pid, COUNT(*) AS games FROM game_players x
              JOIN game_players y ON y.game_id = x.game_id AND y.player_id != x.player_id
              JOIN counting c ON c.game_id = x.game_id
              WHERE x.player_id = ? GROUP BY y.player_id)
            SELECT m.pid, m.games,
              (SELECT COUNT(*) FROM ratings r WHERE r.player_id = m.pid AND r.rater_id != m.pid) AS peer_count,
              (SELECT updated_at FROM ratings r WHERE r.player_id = m.pid AND r.rater_id = ?) AS mine
            FROM mates m JOIN players p ON p.telegram_id = m.pid
            ORDER BY (mine IS NULL OR mine < ?) DESC, peer_count ASC, m.games DESC, m.pid""",
                      rater, rater, stale)

    # --- ratings ---------------------------------------------------------------
    def save_rating(self, player: int, rater: int, scores: list[int | None]) -> bool:
        """Insert or overwrite the (player, rater) rating. All-None saves nothing and returns False."""
        if all(s is None for s in scores):
            return False
        t = iso(now())
        cols = ", ".join(DISCIPLINES)
        marks = ", ".join("?" * len(DISCIPLINES))
        updates = ", ".join(f"{d} = excluded.{d}" for d in DISCIPLINES)
        self.conn.execute(
            f"""INSERT INTO ratings (player_id, rater_id, {cols}, created_at, updated_at)
                VALUES (?, ?, {marks}, ?, ?)
                ON CONFLICT (player_id, rater_id) DO UPDATE SET {updates}, updated_at = excluded.updated_at""",
            (player, rater, *scores, t, t))
        return True

    def ratings(self) -> list[Rating]:
        return [Rating(r["player_id"], r["rater_id"], tuple(r[d] for d in DISCIPLINES), parse(r["updated_at"]))
                for r in self.q("SELECT * FROM ratings")]

    def peer_count(self, uid: int) -> int:
        return self.one("SELECT COUNT(*) FROM ratings WHERE player_id = ? AND rater_id != ?", uid, uid)

    def given_count(self, uid: int) -> int:
        return self.one("SELECT COUNT(*) FROM ratings WHERE rater_id = ? AND player_id != ?", uid, uid)

    # --- export ----------------------------------------------------------------
    def dump(self, table: str) -> tuple[list[str], list[tuple]]:
        assert table in TABLES
        cur = self.conn.execute(f"SELECT * FROM {table}")
        return [c[0] for c in cur.description], [tuple(r) for r in cur.fetchall()]


class _Tx:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE")

    def __exit__(self, exc_type, *_):
        self.conn.execute("ROLLBACK" if exc_type else "COMMIT")
