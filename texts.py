"""All user-facing strings. Level criteria come from criteria.en.yaml."""
from pathlib import Path

import yaml

_CRITERIA = yaml.safe_load((Path(__file__).parent / "criteria.en.yaml").read_text(encoding="utf-8"))
_LEVELS = {lvl["level"]: lvl for lvl in _CRITERIA["levels"]}

DISCIPLINE_NAMES = {
    "serving": "Serving",
    "serve_receive": "Serve receive",
    "setting": "Setting",
    "attacking": "Attacking",
    "blocking": "Blocking",
    "defense": "Defense",
}


def criteria(level: int, discipline: str) -> str:
    return _LEVELS[level]["criteria"][discipline].strip()


NET_NOTE = "Players of both genders should be rated on games with standard male net height of 2.43m."


NOT_MEMBER = "This bot only works for members of the group."

WELCOME = (
    "🏐 <b>Volleyball level bot</b>\n\n"
    "Players of our group rate each other's skill after playing together. "
    "Your level (0.0–6.0) is shown as a tag next to your name in the group.\n\n"
    "• Ratings are anonymous: nobody sees who rated whom.\n"
    "• Your name is not stored. Only your Telegram ID, your ratings and the games you joined.\n"
    "• You can revoke consent and delete all your data at any time.\n\n"
    "Do you agree to take part?"
)
BTN_AGREE = "I agree"
BTN_DECLINE = "No thanks"
DECLINED = "No problem. Send /start if you change your mind."

SELF_RATE_INTRO = (
    "First, rate yourself honestly in 6 disciplines. "
    "This gives you a starting level until other players rate you.\n\n" + NET_NOTE
)
SELF_RATE_DONE = "Thanks! Your starting tag <b>{tag}</b> is now shown in the group."
SELF_RATE_DONE_NO_TAG = (
    "Thanks! Your starting level is <b>{tag}</b>. Telegram doesn't allow tags for group "
    "admins and the owner, so it can't be shown next to your name."
)
SELF_RATE_REQUIRED = "Please rate at least one discipline about yourself. Let's try again."

MENU = "What would you like to do?"
BTN_SUBMIT = "🏐 Submit a game"
BTN_RATE = "⭐ Rate a player"
BTN_STATS = "📊 My stats"
BTN_REVOKE = "🗑 Revoke consent and delete my data"
BTN_MENU = "« Menu"

ASK_GAME_TIME = (
    "Enter the <b>official / announced</b> start time of the game "
    "(e.g. <code>08.11 19:00</code> or <code>08.11.2026 19:00</code>)."
)
BAD_TIME = "I couldn't read that. Please use the format <code>08.11 19:00</code>."
FUTURE_TIME = "The game must be in the past. Submit it after it has started."

PICK_PLAYERS = (
    "Who played on <b>{date} at {time}</b>?\n"
    "Tick at least {min} players, including yourself if you played. (page {page}/{pages})"
)
BTN_DONE = "✔ Done ({n} selected)"
NEED_MIN = "Select at least {min} players."
GAME_EXISTS = (
    "A game on <b>{date} at {time}</b> already exists with these players:\n{existing}\n\n"
    "To be added: {new}\n\n"
    "Submit your participation in a game on {date} at {time}?"
)
ALL_ALREADY_IN = "All selected players are already in the game on {date} at {time}."
BTN_SUBMIT_PARTICIPATION = "Submit"
BTN_CANCEL = "Cancel"
GAME_SAVED = "Saved. The game on {date} at {time} has {n} players. Rate your teammates when you're ready."
SKIPPED_CLASH = "Not added (already in another game within an hour): {names}"
SKIPPED_FULL = "Not added (the game is full): {names}"
BTN_RATE_PLAYERS = "⭐ Rate players"
CANCELLED = "Cancelled."

NO_CANDIDATES = (
    "There's nobody you can rate yet. You can rate players after you've played "
    "a game together with at least 4 players. Submit your games from the menu."
)
CANDIDATES_TOP = (
    "You had a chance to play with these players who really need your rating. "
    "Please spend a few minutes to rate them."
)
CANDIDATES_ALL = "All players you can rate (page {page}/{pages}):"
BTN_SHOW_ALL = "Show all"
NOT_RATEABLE = "You can only rate players you've played a counting game with."

SELF_NAME = "yourself"
BTN_CANT_JUDGE = "Can't judge"
NOTHING_SAVED = "Nothing was saved."
RATING_SAVED = (
    "Thank you for rating {name}. Your rating is saved and will be taken into account soon. "
    "Levels are recalculated every night."
)
EXPIRED = "This screen has expired. Please start again from the menu."

STATS = (
    "📊 <b>Your stats</b>\n\n"
    "Level: <b>{level:.1f}</b> (tag <code>{tag}</code>)\n"
    "{disciplines}\n\n"
    "Peer ratings received: {received}\n"
    "Ratings given: {given}\n\n"
    "<b>Last games</b>\n{games}"
)
STATS_LOCKED = "Your stats appear after at least {n} other players have rated you."

REVOKE_ASK = (
    "Are you sure? This deletes all ratings you gave and received, your game history "
    "and your tag. This cannot be undone."
)
BTN_REVOKE_YES = "Yes, delete everything"
REVOKED = "Done. All your data is deleted. Send /start if you want to join again."


def ago(days: int) -> str:
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 60:
        return f"{days} days ago"
    return f"{days // 30} months ago"
