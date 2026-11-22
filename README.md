# vball-tg-ratebot

Telegram bot where volleyball players in one Telegram group rate each other's skill. Each player's level (0.0–6.0, classic indoor volleyball) is shown as a member tag next to their name in the group, e.g. `John` + tag `[2.5]`.

Ratings are anonymous to users. Only the bot admin can export the raw data.

**Goal for the code: small, readable, easy to maintain.** About 5 files, roughly 1,000 lines. No ORM and no web framework.

## Run and test

Server deployment and Telegram setup are in [DEPLOY.md](DEPLOY.md).

### Setup (once, Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt pytest
copy .env.example .env
```

Fill in `.env` with the **test** bot token, the test group ID and your Telegram user ID. [DEPLOY.md](DEPLOY.md) §1 shows how to find them. In the test group, the bot must be an admin with only **Manage tags** turned on.

### Commands

| What | Command |
|---|---|
| Automated tests | `.\.venv\Scripts\python -m pytest` |
| Run the bot | `.\.venv\Scripts\python bot.py` (stop with Ctrl+C) |
| Recalculate levels and tags now | `.\.venv\Scripts\python nightly.py --once` |

Telegram does not allow tags for the group owner or admins, so test tags with a regular member.

Run only one `bot.py` per token at a time. A second copy, on this PC or on the server, makes Telegram reject the first one's updates.

### Manual checklist (test bot, at least 4 people in the test group)

1. Someone outside the group gets "This bot only works for members of the group".
2. `/start` shows the language choice (English / Русский / Latviešu). After that, everything (consent text, menu, rating screen, criteria) is in the chosen language. **🌐 Language** in the menu changes it later.
3. Consent, then self-rate: the discipline list shows `❓Serve »` everywhere and no **Done**. Rate one discipline → `✅Serve - 3`, still no **Done**. Rate all 6 → **Done** appears; nothing is saved before it. **Done** → the tag `[~~X.X]` appears in the group (not for the owner or admins).
4. **My games** → **Submit a new game** with only yourself selected, or with 2 players but not yourself: **Done** refuses. With yourself and 1 more: the game is saved (it doesn't count for ratings until it has 4). Open it in **My games**, **Add players** to reach 4.
4a. Each added player gets "Did you play?". **Yes** → they count. **No** → removed, and the admin gets a notice naming both players. Leave one unanswered (set `CONFIRM_WITHIN` to 2 minutes for the test) → removed and the admin is notified.
4b. In the player list, send the contact of a consented player → they're ticked. Send the contact of someone who hasn't joined → the invite message. `/export` contains no phone numbers.
5. Submit the same game 30 minutes later with one shared player: the bot offers to add the new players to the existing game and creates nothing.
6. A player already in a 19:00 game is skipped when added to a 19:45 game.
7. **Rate a player** lists only people you played a counting game (4+ players) with.
8. Rating a player: the list shows your current scores. Open a discipline: all 7 levels are visible, your score has ✓. Tap another level → saved, back to the list. **Erase rating** → `❓Serve »`. On an unrated one, **Keep unrated** changes nothing.
9. Run `nightly.py --once`: the logs show tags updated only where the text changed.
10. **My stats** is hidden with 2 peer ratings and shown with 3.
11. `/export` from a non-admin gets no response. From the admin, it returns a zip.
12. Revoke consent, or leave the group: the tag disappears and the user's data is gone (check `/export`).

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
texts.py    loads the translations below; t(lang, key), criteria(), ago()
locales/{en,ru,lv}.yaml     all UI strings per language
criteria.{en,ru,lv}.yaml    level criteria per language
```

UI languages: **English, Russian, Latvian.** `en` is the reference: the other locale files must have the same keys and `{placeholders}` (a test checks this). In all three criteria files, the original division codes are replaced with level numbers (B = 1, BB = 2–3, A = 4–5, AA = 6).

## 2. Telegram setup and permissions

- The bot is an **administrator** of the group with only the **`can_manage_tags`** right. All other admin rights are off: it cannot delete, ban, pin or invite.
- The bot **never sends messages in the group**. It only talks to users in private chat.
- Group privacy mode stays ON, so the bot does not read group messages.
- The bot receives `chat_member` updates so it knows when someone leaves the group.
- Tags are set with `setChatMemberTag(chat_id, user_id, tag)`. A tag is at most 16 characters, emoji are not allowed, and an empty tag removes it. Group admins and the owner may not accept member tags. If the call fails, log it and skip that user.
- No Mini Apps.

## 3. Privacy

- Stored per user: Telegram ID, consent time, UI language, ratings, game participation. **Names are not stored.** When a screen needs a name, fetch it live with `getChatMember` (`first_name`, plus `@username` if present).
- Game records contain only the start time and the participants (who added whom, and whether they confirmed).
- **Shared contacts** are used only to read the Telegram user id of the person to add. The phone number and contact name are never stored or logged.
- If the database leaks, the most sensitive information is who played with whom.

## 4. Onboarding

1. The user opens the bot in private chat. The bot checks the user is a member of `GROUP_ID`. If not, it replies "This bot only works for members of the group" and stops.
2. **Language**: the bot asks "Choose language / Выберите язык / Izvēlieties valodu" with English / Русский / Latviešu buttons. Before consent the choice is kept only in memory. It is stored with the player on consent. Messages to unknown users (e.g. "only for members") use the Telegram app language.
3. The bot shows the description and asks for consent: **[I agree] / [No thanks]**. Consent is recorded as `consent_at`.
4. **Self-rating** (required before anything else): the same discipline list and level screens as rating someone else (§7). **All 6 disciplines are required**; the scores are kept in the conversation state and saved together on **Done**. It is saved as a rating where `rater = player`.
5. **On Done, set the tag** `[~~X.X]` (see §9).
6. Show the main menu.

Users who have not consented never get a tag and can't be rated.

## 5. Main menu

1. My games (with **Submit a new game** inside)
2. Rate a player
3. My stats (only shown with ≥3 peer ratings received)
4. Language
5. Revoke consent and delete my data

Admin only: `/export`.

## 6. Games

### My games (menu 1)

A list of every game I played in, newest first, 10 per page. Each button shows the date with the weekday, time, number of players and the game's average level (`08.11 (Sun) 19:00 · 👤 6 · [3.2]`; no average while nobody in it has a level; ` ⏳` if my own participation isn't confirmed yet). Below the list: **[Submit a new game]** and **[Menu]**.

Opening a game shows its date (with weekday) and time, below it the average level ("Average level: 3.2"), and all its players with their current tag, as in the group (`• Anna (@anna) [~3.5]`; names fetched live, tags from `players.tag`; ⏳ marks players who haven't confirmed yet), plus "This game will count for ratings once it has at least 4 players." if it has fewer. Buttons: **[Add players]** and **[« My games]**. **Add players** opens the same checkbox list as below, without the players already in the game; at least one must be ticked. The same rules apply (±1h clash, max 100). Only players of a game can open it.

### Submit a new game (inside My games)

Any player can submit a game. There are no join links.

1. The bot asks for the start time: "Enter the **official / announced** start time of the game (e.g. `08.11 19:00` or `08.11.2026 19:00`)." The time is read in `TZ` and stored in UTC. If no year is given, use the current year; if that date is in the future, use the previous year.
2. The game must be in the past.
3. **Select the players**: a checkbox list of all consented players (names fetched live, the submitter first and pre-selected, 10 per page). **At least 2 players, including the submitter,** must be selected. Such a game is saved but only **counts** once it has 4+ players (see Rules); more players can be added later from My games.
4. If a game exists **within ±1 hour that shares at least one selected player**, nothing new is created. The bot shows that game's players and the players to be added, and asks "Submit your participation in a game on 08.11 at 19:00?" with **[Submit] / [Cancel]**. Submitting adds the selected players (the submitter included, if selected) to the existing game. A game in the same hour with no shared players is a different game (e.g. another court) and is created separately.
5. Otherwise create the game with the selected players.
6. A player already in **another** game within ±1h is skipped with a note. A game holds at most 100 players.
7. Reply with "Saved. The game … has N players." and a **[Rate players]** button.

In both player lists (new game and Add players), a player can also be added by **sending their contact** (📎 → Contact). The bot reads only the contact's Telegram user id. If the contact has no Telegram account, or that person hasn't consented yet, nothing is added: the bot says so and suggests inviting them with the bot link (they must press Start, give consent and be a member of the group).

The game record still holds only the start time and the players.

### Confirming participation

- Whoever adds players is confirmed at once. **Everyone else they add must confirm**: the bot sends them (in their language) "{name} added you to the game on 08.11 at 19:00. Did you play in it?" with **[✅ Yes, I played] [❌ No, I didn't]**. Submitting your own participation in an existing game also confirms you.
- **Yes**: confirmed. **No**: removed from the game at once. **No answer within 48 hours** (checked every 10 minutes): removed, and told so.
- Each removal sends the bot admin (`ADMIN_ID`) a notice: the game (id, date, time), the removed player and who added them (live names, @usernames and ids) and the reason ("answered 'I didn't play'" / "no answer within 48 hours").
- **Pending players don't count**: not for "games together", the counting threshold, rate candidates or weights. All calculations read the `played` view (confirmed rows only). Clash checks and the 100-player limit do include pending players.

### Rules

- A game **counts** (for "games together" and weights) only if it has **≥4 confirmed players**. Games with fewer players are kept but ignored in all calculations.
- Max 100 players per game.

## 7. Rating

### Choosing whom to rate (menu 2)

Candidates are all consented players with **≥1 counting game together** with the rater, excluding the rater. Players without a shared game can't be rated.

Sort the candidates by:
1. "needs my rating" first: not rated by me yet, **or** my rating is older than 90 days, **or** my rating still leaves some disciplines unrated (`❓`)
2. fewest peer ratings received
3. most games together

Show the **top 5 that need my rating**, with "You had a chance to play with these players who really need your rating. Please spend a few minutes to rate them." Players I fully rated (all 6 disciplines) within 90 days are never on this first screen; if nobody needs my rating, say "You have recently rated all the players you played with…". A **[Show all]** button lists all candidates in the same order, 10 per page, with recently rated players at the end marked ✓ (they can still be re-rated).

### Rating screen

Everything happens in **one message** that is edited in place. Every tap saves at once; there is no final
"Save" step.

1. **Discipline list** after a player is picked:
   "You have selected {name} for rating. / Games together: N, last: {ago}. / You rate this player for the first
   time. *(or)* You have already rated this player {ago}. / \*Players of both genders should be rated on games
   with standard male net height of 2.43m. / Choose the discipline to rate/rerate for {name}:"
   Then one button per discipline with my current score for this player: `❓Serve »` (no score yet), `✅Receive - 3`, …
   (short names: Serve, Receive, Set, Attack, Block, Defense), and **[« Back to menu]**.
2. **Level screen** when a discipline is tapped: "Rate {name} skill:", the discipline in bold, then **all 7
   levels in one plain text block**, one line each, the level number in bold: **•0•** `<criteria>` … **•6•** `<criteria>` (no emojis, so the lines
   stay tight; about 2000 characters, well below Telegram's 4096). Levels are shown only as numbers 0–6, with no
   division codes (A, BB…). Buttons:
   - row 1: **[0] … [6]**, with `✓` next to my current score (`3✓`). Tapping a level saves it and returns to the
     list (a short "Saved" notice pops up); tapping the ✓ level returns without a change.
   - row 2, only if this discipline has a score: **[Erase rating •N• for {discipline}]** sets it back to NULL.
   - row 3, only if it has no score: **[Keep unrated, go back to list]**.
3. Saving: **one rating per (player, rater) pair**, one column per discipline. Each change updates only that
   discipline and sets `updated_at = now` (this restarts the 90-day "needs my rating" clock and `w_age` for the
   whole rating). Erasing the last scored discipline deletes the rating.
4. **Self-rating** uses the same two screens. The list starts with the self-rating intro, and instead of
   "Back to menu" has **[✅ Done]** once all 6 disciplines are rated. Unlike a peer rating, nothing is saved per
   tap: Done saves all six at once, sets the tag (§9) and shows the menu. (If the bot restarts mid-way, the
   self-rating starts over.)
   The main menu is not available until Done, so the self-rating list also has **[🌐 Language]** and
   **[🗑 Revoke consent and delete my data]**. Changing the language, cancelling the revoke question, or sending
   /start returns to the self-rating with the scores collected so far.

## 8. Level calculation (`rating.py`)

Runs nightly at **00:00 UTC**, in one pass, using **last night's stored levels** as input. Nothing derived is stored except `players.level`, `players.tag`, `players.tag_shown` and the game averages (`games.avg_level`, `games.rated_n`).

**Game average level.** `avg_level` = average of the stored `players.level` over the game's **confirmed** players who have a level (`rated_n` of them); NULL if none has one. It uses players' *current* levels, so it moves as they improve. It is recalculated by `DB.refresh_avg()`, one SQL `UPDATE`: for all games in the nightly run (after levels are stored) and at startup; for one game whenever its confirmed roster changes (new game, players added, a confirmation); for all games when a user is deleted. A player's first self-rating stores their level at once so it shows before the nightly run.

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
| Only a self-rating | `[~~2.5]` |
| `peer_weight_sum < 1.5` | `[~2.5]` |
| `peer_weight_sum ≥ 1.5` | `[2.5]` |

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

Overall = 15.0 / 6 = **2.5**. `peer_weight_sum = 1.5`, which is ≥ 1.5, so the tag is **`[2.5]`**.

## 9. Tags

The tag text is `[{prefix}{level:.1f}]` (in square brackets, e.g. `[~~2.3]`, `[4.1]`) with prefix `~~`, `~` or nothing (§8). Update it:

- **right after the self-rating** (`[~~X.X]`)
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
  tag_shown    TEXT,                   -- tag currently set in the group
  lang         TEXT NOT NULL DEFAULT 'en', -- UI language: en, ru, lv
  tag          TEXT                    -- current tag text, stored with `level` (shown in game details)
);

CREATE TABLE games (
  id           INTEGER PRIMARY KEY,
  start_at     TEXT NOT NULL,          -- UTC ISO
  created_by   INTEGER NOT NULL,
  created_at   TEXT NOT NULL,
  avg_level    REAL,                   -- average players.level of confirmed players with a level
  rated_n      INTEGER NOT NULL DEFAULT 0  -- how many players that average is over
);

CREATE TABLE game_players (
  game_id      INTEGER NOT NULL REFERENCES games(id),
  player_id    INTEGER NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
  joined_at    TEXT NOT NULL,
  added_by     INTEGER,          -- who added this player; NULL = added themselves
  confirmed_at TEXT,             -- NULL = waiting for the player to confirm
  PRIMARY KEY (game_id, player_id)
);
CREATE VIEW played AS SELECT * FROM game_players WHERE confirmed_at IS NOT NULL;

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
2. A player with only a self-rating gets tag `[~~X.X]` right after pressing Done on the self-rating.
3. Submitting a game 30 minutes away from an existing game that shares a player offers to add the players to it and creates nothing. Without a shared player, a new game is created.
4. A player already in a game at 19:00 is not added to another game at 19:45.
5. A game with 3 players does not count toward "games together", and its players can't rate each other through it.
6. A user cannot rate someone they have no counting game with.
7. Changing one discipline of an existing rating updates only that column and `updated_at`; the row stays one per (player, rater).
8. Erasing the last scored discipline deletes the rating.
9. The level screen shows all 7 levels' criteria in one message, marks the current score with ✓, and offers Erase (scored) or Keep unrated (not scored).
10. Revoking consent removes the user's ratings (given and received), game memberships and tag. The nightly run then recalculates the affected players.
11. The nightly run calls `setChatMemberTag` only for players whose tag text changed.
12. "My stats" is hidden with 2 peer ratings and shown with 3.
13. `/export` from a non-admin gets no response.
14. Every UI string and every criteria text exists in English, Russian and Latvian, with the same placeholders.
