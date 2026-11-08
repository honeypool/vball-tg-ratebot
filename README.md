# vball-tg-ratebot

Telegram bot where volleyball players in one Telegram group rate each other's skill. Each player's level (0.0–6.0, classic indoor volleyball) is shown as a member tag next to their name in the group, e.g. `John` + tag `2.5`.

Ratings are anonymous to users. Only the bot admin can export the raw data.

**Goal for the code: small, readable, easy to maintain.** About 5 files, roughly 1,000 lines. No ORM and no web framework.

---

## 1. Tech stack

- Python 3.12, **aiogram 3**, the standard library `sqlite3`
- Nightly job: a single asyncio task (or APScheduler) inside the bot process
- Runs in Docker on a small VPS
- Config in `.env`:
  - `BOT_TOKEN`
  - `GROUP_ID`: the one group the bot serves
  - `ADMIN_ID`: Telegram ID of the bot admin
  - `TZ=Europe/Riga`: the time zone players type game times in. Everything is stored in UTC.

```
bot.py      handlers, menus, conversation flows
db.py       schema + all SQL queries
rating.py   compute_levels(): pure math, no DB or Telegram code
nightly.py  nightly recalculation, tag updates, CSV export
texts.py    all UI strings; criteria come from criteria.en.yaml
```

UI language: English only.

## 2. Telegram setup and permissions

- The bot is an **administrator** of the group with only the **`can_manage_tags`** right. All other admin rights are off: it cannot delete, ban, pin or invite.
- The bot **never sends messages in the group**. It only talks to users in private chat.
- Group privacy mode stays ON, so the bot does not read group messages.
- The bot receives `chat_member` updates so it knows when someone leaves the group.
- Tags are set with `setChatMemberTag(chat_id, user_id, tag)`. A tag is at most 16 characters, emoji are not allowed, and an empty tag removes it. Group admins and the owner may not accept member tags. If the call fails, log it and skip that user.
- No Mini Apps.

## 3. Privacy

- Stored per user: Telegram ID, consent time, ratings, game participation. **Names are not stored.** When a screen needs a name, fetch it live with `getChatMember` (`first_name`, plus `@username` if present).
- Game records contain only the start time and the participants.
- If the database leaks, the most sensitive information is who played with whom.

## 4. Onboarding

1. The user opens the bot in private chat. The bot checks the user is a member of `GROUP_ID`. If not, it replies "This bot only works for members of the group" and stops.
2. The bot shows the description and asks for consent: **[I agree] / [No thanks]**. Consent is recorded as `consent_at`.
3. **Self-rating** (required before anything else): the same 6-discipline flow as rating someone else (§7). It is saved as a rating where `rater = player`.
4. **Right after the self-rating, set the tag** `~~X.X` (see §9).
5. Show the main menu.

Users who have not consented never get a tag and can't be rated.

## 5. Main menu

1. Submit a game
2. Rate a player
3. My stats (only shown with ≥3 peer ratings received)
4. Revoke consent and delete my data

Admin only: `/export`.

## 6. Games

### Submit a game (menu 1)

1. The bot asks for the start time: "Enter the **official / announced** start time of the game (e.g. `08.11 19:00` or `08.11.2026 19:00`)." The time is read in `TZ` and stored in UTC. If no year is given, use the current year; if that date is in the future, use the previous year.
2. The game must be in the past.
3. If **any game exists with a start time within ±1 hour**: reply "A game at 19:00 on 08.11 already exists, join it?" with **[Join]**. Do not create a new one.
4. Otherwise create the game. **The submitter automatically joins it.**
5. Reply with a join link `https://t.me/<bot>?start=g<game_id>` and the text: "Share this link in the group so players can join."

### Join a game (via `/start g<id>`)

1. If the user has not consented or self-rated yet, run onboarding first, then continue joining.
2. Show "Join the game on 08.11 at 19:00?" with **[Join] / [Cancel]**.
3. Reject with a message if:
   - the user is already in this game
   - the user is in **another** game within ±1h of it
   - the game already has 100 players
4. After joining: "You're in. Rate your teammates when you're ready." with a **[Rate players]** button. This is the only reminder the bot ever sends.

### Rules

- A game **counts** (for "games together" and weights) only if it has **≥4 players**. Games with fewer players are kept but ignored in all calculations.
- Max 100 players per game.

## 7. Rating

### Choosing whom to rate (menu 2)

Candidates are all consented players with **≥1 counting game together** with the rater, excluding the rater. Players without a shared game can't be rated.

Sort the candidates by:
1. "needs my rating" first: not rated by me yet, **or** my rating is older than 90 days
2. fewest peer ratings received
3. most games together

Show the **top 5**, with "You had a chance to play with these players who really need your rating. Please spend a few minutes to rate them." Below them, a **[Show all]** button lists all candidates in the same order, 10 per page.

### Rating screen

1. Header: player name, games played together, time since the last game together.
2. Ask the 6 disciplines one at a time: Serving, Serve receive, Setting, Attacking, Blocking, Defense.
3. For each discipline, send one **rich message** (`sendRichMessage`) with a table of the **5 levels nearest the player's current level**. Center on the level rounded to an integer and clamp to 0–6: level 0.4 shows levels 0–4, 3.2 shows 1–5, 5.8 shows 2–6. Criteria text comes from `criteria.en.yaml`. No classic-HTML fallback.
4. Buttons: one per shown level, **[◀]** / **[▶]** to shift the window by one level (within 0–6), and **[Can't judge]**, which saves NULL for that discipline.
5. Self-rating: there is no current level yet, so the first discipline opens centered on 3. Each later discipline opens centered on the answer given for the previous one.
6. At least one discipline must be answered. If all six are "Can't judge", save nothing and show "Nothing was saved."
7. Saving: **one rating per (player, rater) pair.** Rating again overwrites the scores and sets `updated_at = now`.
8. Confirmation: "Thank you for rating {name}. Your rating is saved and will be taken into account soon. Levels are recalculated every night."

## 8. Level calculation (`rating.py`)

Runs nightly at **00:00 UTC**, in one pass, using **last night's stored levels** as input. Nothing derived is stored except `players.level` and `players.tag_shown`.

### Weight of one rating

**Self-rating:** `w = 0.3`. No other factors apply.

**Peer rating:**

```
w = w_games × w_gap × w_age

w_games = g / (g + 3)
          g = number of counting games (≥4 players) the two have played together, as of now

w_gap   = max(0.25, 1 − 0.25 × max(0, player_level − rater_level))
          both levels are from last night (overall level, unrounded)
          lowers the weight only when the rater is weaker than the player

w_age   = 0.5 ^ (age_days / 180)
          age_days = days since the rating's updated_at
```

### Player level

1. **Per discipline:** the weighted average of all non-NULL scores for that discipline, self-rating included.
2. **Overall:** the plain average of the discipline levels that have a value. Disciplines with no scores at all are left out.
3. **Shown value:** the overall level rounded to 0.1.

### Confidence (tilde)

`peer_weight_sum` = the sum of `w` over all **peer** ratings of the player. The self-rating is not included.

| Situation | Tag |
|---|---|
| Only a self-rating | `~~2.5` |
| `peer_weight_sum < 1.5` | `~2.5` |
| `peer_weight_sum ≥ 1.5` | `2.5` |

Because `w_age` shrinks over time, a player whose ratings are old can get the tilde back. This is intended.

All constants (0.3, 3, 0.25, 180, 1.5, 90, ±1h, 4 and 100 players) live at the top of `rating.py` / `bot.py`.

### Worked example (use as a test)

Weight of one peer rating: the rater is at 2.0, the player at 3.5, they played 2 games together, and the rating is 90 days old.
`w_games = 2/5 = 0.4`, `w_gap = 1 − 0.25×1.5 = 0.625`, `w_age = 0.5^0.5 = 0.7071`, so **w = 0.177**.

Level of Anna, who has two peer ratings (the self-rating is left out to keep it short):

| | w | Serve | Receive | Set | Attack | Block | Defense |
|---|---|---|---|---|---|---|---|
| Rating 1 | 1.0 | 3 | 3 | 2 | 3 | NULL | 3 |
| Rating 2 | 0.5 | 2 | 3 | 2 | 2 | 2 | 2 |
| Result | | 2.67 | 3.0 | 2.0 | 2.67 | 2.0 | 2.67 |

Overall = 15.0 / 6 = **2.5**. `peer_weight_sum = 1.5`, which is ≥ 1.5, so the tag is **`2.5`**.

## 9. Tags

The tag text is `{prefix}{level:.1f}` with prefix `~~`, `~` or nothing (§8). Update it:

- **right after the self-rating** (`~~X.X`)
- **nightly**, only if the new text differs from `players.tag_shown`
- **when consent is revoked or the user leaves the group**: remove the tag (empty string), immediately

## 10. My stats (menu 3)

Shown only if the player has **≥3 peer ratings**. This threshold protects raters' anonymity. Computed live with `compute_levels()`:

- current level and tag
- level per discipline
- peer ratings received, ratings given
- last 10 games (date and time in `TZ`, number of players)

## 11. Revoke consent / leaving the group

Revoking consent (menu 4, with an "Are you sure?" step) and leaving the group (a `chat_member` update) do the same thing:

- delete all ratings **given and received** by the user
- delete the user's rows in `game_players`. The games stay.
- delete the `players` row
- remove the tag

The user can consent again later and starts from zero.

## 12. Admin

`/export`, available only to `ADMIN_ID`: the bot sends a zip file with one CSV per table. There are no other admin tools in v1. Anomalies are checked manually from the export.

## 13. Data model (SQLite)

```sql
CREATE TABLE players (
  telegram_id  INTEGER PRIMARY KEY,
  consent_at   TEXT NOT NULL,          -- UTC ISO
  level        REAL,                   -- last nightly overall level, NULL before first run
  tag_shown    TEXT                    -- tag currently set in the group
);

CREATE TABLE games (
  id           INTEGER PRIMARY KEY,
  start_at     TEXT NOT NULL,          -- UTC ISO
  created_by   INTEGER NOT NULL,
  created_at   TEXT NOT NULL
);

CREATE TABLE game_players (
  game_id      INTEGER NOT NULL REFERENCES games(id),
  player_id    INTEGER NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
  joined_at    TEXT NOT NULL,
  PRIMARY KEY (game_id, player_id)
);

CREATE TABLE ratings (
  player_id     INTEGER NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
  rater_id      INTEGER NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
  serving       INTEGER,               -- 0..6 or NULL ("can't judge")
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
```

"Is self-rated" means a row exists with `rater_id = player_id`. Counts (games played, ratings given or received) are always computed with queries and never stored.

## 14. Acceptance tests

1. `rating.py` reproduces both worked examples in §8 exactly.
2. A player with only a self-rating gets tag `~~X.X` right after finishing the self-rating.
3. Submitting a game 30 minutes away from an existing game offers [Join] and creates nothing.
4. A user already in a game at 19:00 cannot join another game at 19:45.
5. A game with 3 players does not count toward "games together", and its players can't rate each other through it.
6. A user cannot rate someone they have no counting game with.
7. Rating the same player twice leaves one row with the new scores and a new `updated_at`.
8. Choosing "Can't judge" for all 6 disciplines saves nothing.
9. The rating window for a player at 0.4 shows levels 0–4, and ◀ / ▶ shift it within 0–6.
10. Revoking consent removes the user's ratings (given and received), game memberships and tag. The nightly run then recalculates the affected players.
11. The nightly run calls `setChatMemberTag` only for players whose tag text changed.
12. "My stats" is hidden with 2 peer ratings and shown with 3.
13. `/export` from a non-admin gets no response.
