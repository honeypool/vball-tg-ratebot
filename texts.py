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


def division(level: int) -> str:
    return _LEVELS[level]["division"]


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
    "This gives you a starting level until other players rate you."
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
GAME_EXISTS = "A game at {time} on {date} already exists, join it?"
GAME_CREATED = (
    "Game on {date} at {time} created, and you're in.\n\n"
    "Share this link in the group so players can join:\n{link}"
)
BTN_JOIN = "Join"
BTN_CANCEL = "Cancel"

JOIN_ASK = "Join the game on {date} at {time}?"
JOINED = "You're in. Rate your teammates when you're ready."
BTN_RATE_PLAYERS = "⭐ Rate players"
JOIN_ERRORS = {
    "missing": "This game doesn't exist.",
    "already": "You're already in this game.",
    "clash": "You're already in another game within an hour of this one.",
    "full": "This game is full.",
}
CANCELLED = "Cancelled."

NO_CANDIDATES = (
    "There's nobody you can rate yet. You can rate players after you've played "
    "a game together with at least 4 players. Join games via the links shared in the group."
)
CANDIDATES_TOP = (
    "You had a chance to play with these players who really need your rating. "
    "Please spend a few minutes to rate them."
)
CANDIDATES_ALL = "All players you can rate (page {page}/{pages}):"
BTN_SHOW_ALL = "Show all"
NOT_RATEABLE = "You can only rate players you've played a counting game with."

RATE_HEADER = (
    "Rating <b>{name}</b>\n"
    "Games together: {games}\n"
    "Last game together: {ago}"
)
DISCIPLINE_TITLE = "{name}: {discipline} ({step}/6)"
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
