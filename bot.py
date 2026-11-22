"""Volleyball rating bot: handlers, menus and conversation flows (README §4–§12)."""
from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import time
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import LEAVE_TRANSITION, ChatMemberUpdatedFilter, Command, CommandStart
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (BotCommand, BufferedInputFile, CallbackQuery, ChatMemberUpdated, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)

import nightly
import texts as T
from db import DB, MAX_PLAYERS, MIN_COUNTING_PLAYERS, MIN_SUBMIT_PLAYERS, Added, iso, now, parse
from rating import DISCIPLINES, MAX_LEVEL, MIN_LEVEL


def _load_dotenv(path: Path) -> None:
    """Tiny .env reader so local runs need no extra package. Real env vars win."""
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(Path(__file__).parent / ".env")

# --- config & constants --------------------------------------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
GROUP_ID = int(os.environ.get("GROUP_ID", "0"))
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
TZ = ZoneInfo(os.environ.get("TZ", "Europe/Riga"))
DB_PATH = os.environ.get("DB_PATH", "bot.sqlite")

RERATE_AFTER = timedelta(days=90)  # "needs my rating" again after this long
TOP_N = 5                          # candidates on the first screen
PAGE_SIZE = 10                     # candidates per page in "Show all"
STATS_MIN_PEERS = 3                # peer ratings needed to see "My stats"
CONFIRM_WITHIN = timedelta(hours=48)  # an added player must confirm within this, or is removed
EXPIRY_CHECK_SECONDS = 600
MEMBER_CACHE_SECONDS = 300
ERASE, KEEP = -1, -2               # RateSet values besides 0..6: "Erase rating", "Keep unrated"

log = logging.getLogger("bot")
db: DB  # set in main()
router = Router()
Tr = Callable[..., str]            # tr("KEY", **kw) -> text in the user's language


# --- callback data ---------------------------------------------------------------
class Menu(CallbackData, prefix="m"):
    action: str        # home, games, submit, rate, stats, language, revoke, revoke_yes, consent, decline


class Lang(CallbackData, prefix="l"):
    code: str


class MyGames(CallbackData, prefix="mg"):
    kind: str          # list (value = page), open (value = game id), add (value = game id)
    value: int


class Confirm(CallbackData, prefix="c"):
    gid: int
    yes: int           # 1 = I played, 0 = I didn't


class GamePick(CallbackData, prefix="g"):
    kind: str          # toggle (value = player id), page, done, add (value = game id), cancel
    value: int


class Page(CallbackData, prefix="p"):
    page: int          # -1 = top 5


class Pick(CallbackData, prefix="r"):
    pid: int


class RateOpen(CallbackData, prefix="ro"):
    pid: int           # player being rated (= me: self-rating)
    d: int             # discipline index


class RateSet(CallbackData, prefix="rs"):
    pid: int
    d: int
    v: int             # 0..6 = level, ERASE, KEEP


class Flow(StatesGroup):
    game_time = State()
    game_players = State()


# --- helpers ---------------------------------------------------------------------
def fmt_local(start_at: str) -> tuple[str, str]:
    t = parse(start_at).astimezone(TZ)
    return t.strftime("%d.%m"), t.strftime("%H:%M")


def fmt_day(start_at: str, lang: str) -> tuple[str, str]:
    """Like fmt_local, with the local weekday: ("08.11 (Sun)", "19:00")."""
    date, hm = fmt_local(start_at)
    return f"{date} ({T.weekday(lang, parse(start_at).astimezone(TZ).weekday())})", hm


def keyboard(rows: list[list[tuple[str, CallbackData]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=cd.pack()) for t, cd in row] for row in rows])


_member_cache: dict[int, tuple[float, bool]] = {}
_name_cache: dict[int, tuple[float, str]] = {}
_lookups = asyncio.Semaphore(10)  # parallel getChatMember calls when listing many players


async def is_member(bot: Bot, uid: int) -> bool | None:
    """True / False, or None if Telegram could not be asked."""
    hit = _member_cache.get(uid)
    if hit and time.monotonic() - hit[0] < MEMBER_CACHE_SECONDS:
        return hit[1]
    try:
        m = await bot.get_chat_member(GROUP_ID, uid)
    except TelegramAPIError as e:
        log.warning("getChatMember(%s) failed: %s", uid, e)
        return None
    ok = m.status in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.MEMBER) or (
        m.status == ChatMemberStatus.RESTRICTED and getattr(m, "is_member", False))
    _member_cache[uid] = (time.monotonic(), ok)
    if m.user:
        _name_cache[uid] = (time.monotonic(), _display(m.user))
    return ok


def _display(user) -> str:
    return user.first_name + (f" @{user.username}" if user.username else "")


def with_handle(display: str) -> str:
    """'Anna @anna' -> 'Anna (@anna)'; a name without a username stays as it is."""
    name, sep, handle = display.rpartition(" @")
    return f"{name} (@{handle})" if sep else display


async def name_of(bot: Bot, uid: int, lang: str) -> str:
    """Names are never stored: fetched live, cached in memory for a few minutes."""
    hit = _name_cache.get(uid)
    if hit and time.monotonic() - hit[0] < MEMBER_CACHE_SECONDS:
        return hit[1]
    try:
        async with _lookups:
            m = await bot.get_chat_member(GROUP_ID, uid)
    except TelegramAPIError:
        return T.t(lang, "PLAYER")
    name = _display(m.user)
    _name_cache[uid] = (time.monotonic(), name)
    return name


async def names_list(bot: Bot, uids, lang: str) -> str:
    return ", ".join(html.escape(n) for n in await asyncio.gather(*(name_of(bot, u, lang) for u in uids)))


def live_levels():
    return nightly.levels(db)


async def forget_user(bot: Bot, uid: int) -> None:
    """Revoke consent / left the group: delete data and remove the tag (README §11)."""
    existed = db.delete_user(uid)
    _member_cache.pop(uid, None)
    _name_cache.pop(uid, None)
    if existed:
        await nightly.set_tag(bot, GROUP_ID, uid, "")
        log.info("user %s deleted", uid)


class MembersOnly(BaseMiddleware):
    """Private chats only, and only for members of GROUP_ID (README §4.1).
    Also resolves the user's language and passes `lang` and `tr` to handlers."""

    async def __call__(self, handler, event, data):
        chat, user = data.get("event_chat"), data.get("event_from_user")
        if chat is None or chat.type != "private" or user is None:
            return None
        member = await is_member(data["bot"], user.id)
        if member is False and db.player(user.id):
            await forget_user(data["bot"], user.id)   # missed the "left" update
        if not member:
            text = T.t(T.lang_from_telegram(user.language_code), "NOT_MEMBER")
            if isinstance(event, CallbackQuery):
                await event.answer(text, show_alert=True)
            else:
                await event.answer(text)
            return None
        data["lang"] = lang = await user_lang(user, data.get("state"))
        data["tr"] = partial(T.t, lang)
        return await handler(event, data)


async def user_lang(user, state: FSMContext | None) -> str:
    """Stored choice -> choice made during onboarding (not stored before consent) -> Telegram app language."""
    p = db.player(user.id)
    if p:
        return T.norm(p["lang"])
    if state:
        chosen = (await state.get_data()).get("lang")
        if chosen:
            return T.norm(chosen)
    return T.lang_from_telegram(user.language_code)


# --- menu ------------------------------------------------------------------------
def menu_kb(uid: int, tr: Tr) -> InlineKeyboardMarkup:
    rows = [[(tr("BTN_MY_GAMES"), Menu(action="games"))], [(tr("BTN_RATE"), Menu(action="rate"))]]
    if db.peer_count(uid) >= STATS_MIN_PEERS:
        rows.append([(tr("BTN_STATS"), Menu(action="stats"))])
    rows.append([(tr("BTN_LANGUAGE"), Menu(action="language"))])
    rows.append([(tr("BTN_REVOKE"), Menu(action="revoke"))])
    return keyboard(rows)


def home_kb(tr: Tr) -> InlineKeyboardMarkup:
    return keyboard([[(tr("BTN_MENU"), Menu(action="home"))]])


async def show_menu(message: Message, uid: int, tr: Tr) -> None:
    await message.answer(tr("MENU"), reply_markup=menu_kb(uid, tr))


def ready(uid: int) -> bool:
    return db.player(uid) is not None and db.is_self_rated(uid)


def lang_kb() -> InlineKeyboardMarkup:
    return keyboard([[(T.t(code, "LANG_NAME"), Lang(code=code))] for code in T.LANGS])


def consent_kb(tr: Tr) -> InlineKeyboardMarkup:
    return keyboard([[(tr("BTN_AGREE"), Menu(action="consent")), (tr("BTN_DECLINE"), Menu(action="decline"))]])


# --- onboarding (README §4) ------------------------------------------------------
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, tr: Tr, lang: str):
    uid = message.from_user.id
    await state.clear()
    if db.player(uid) is None:
        await message.answer(T.CHOOSE_LANG, reply_markup=lang_kb())
    elif not db.is_self_rated(uid):
        await start_self_rating(message, state, uid, tr, lang)
    else:
        await show_menu(message, uid, tr)


@router.callback_query(Menu.filter(F.action == "language"))
async def on_language_menu(cb: CallbackQuery):
    await cb.answer()
    await cb.message.answer(T.CHOOSE_LANG, reply_markup=lang_kb())


@router.callback_query(Lang.filter())
async def on_lang(cb: CallbackQuery, callback_data: Lang, state: FSMContext):
    await cb.answer()
    uid, lang = cb.from_user.id, T.norm(callback_data.code)
    tr = partial(T.t, lang)
    await cb.message.edit_text(tr("LANG_SET"))
    if db.player(uid) is None:
        # not consented yet: keep the choice in memory only, store it on consent
        await state.update_data(lang=lang)
        return await cb.message.answer(tr("WELCOME"), reply_markup=consent_kb(tr))
    db.set_lang(uid, lang)
    if ready(uid):
        await state.clear()
        await show_menu(cb.message, uid, tr)
    else:
        await start_self_rating(cb.message, state, uid, tr, lang)


@router.callback_query(Menu.filter(F.action == "consent"))
async def on_consent(cb: CallbackQuery, state: FSMContext, tr: Tr, lang: str):
    await cb.answer()
    uid = cb.from_user.id
    db.add_player(uid, lang)
    await cb.message.edit_reply_markup(reply_markup=None)
    if db.is_self_rated(uid):
        await state.clear()
        await show_menu(cb.message, uid, tr)
    else:
        await start_self_rating(cb.message, state, uid, tr, lang)


@router.callback_query(Menu.filter(F.action == "decline"))
async def on_decline(cb: CallbackQuery, state: FSMContext, tr: Tr):
    await cb.answer()
    await state.clear()
    await cb.message.edit_text(tr("DECLINED"))


async def start_self_rating(message: Message, state: FSMContext, uid: int, tr: Tr, lang: str):
    await state.clear()
    await show_disciplines(message, message.bot, uid, uid, tr, lang, new=True)


# --- games (README §6) -----------------------------------------------------------
GAME_TIME_RE = re.compile(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\s+(\d{1,2})[:.](\d{2})")


def parse_game_time(text: str, now_utc: datetime) -> datetime | None:
    """`08.11 19:00` or `08.11.2026 19:00` in TZ -> aware UTC datetime. No year: this year, or last
    year if that would be in the future."""
    m = GAME_TIME_RE.fullmatch(text.strip())
    if not m:
        return None
    day, month, year, hour, minute = m.groups()
    local_now = now_utc.astimezone(TZ)
    try:
        t = datetime(int(year or local_now.year), int(month), int(day), int(hour), int(minute), tzinfo=TZ)
        if year is None and t > local_now:
            t = t.replace(year=t.year - 1)
    except ValueError:
        return None
    return t.astimezone(timezone.utc)


def games_screen(games: list, page: int, tr: Tr, lang: str) -> tuple[str, InlineKeyboardMarkup]:
    """My games: one button per game (newest first), then [Submit a new game] and [Menu]."""
    pages = max(1, -(-len(games) // PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    rows = []
    for g in games[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
        date, hm = fmt_day(g["start_at"], lang)
        label = tr("GAME_ROW", date=date, time=hm, n=g["n"])
        if g["avg_level"] is not None:
            label += tr("GAME_ROW_AVG", avg=f"{g['avg_level']:.1f}")
        label += " ⏳" if g["mine_pending"] else ""
        rows.append([(label, MyGames(kind="open", value=g["id"]))])
    nav = []
    if page > 0:
        nav.append(("◀", MyGames(kind="list", value=page - 1)))
    if page < pages - 1:
        nav.append(("▶", MyGames(kind="list", value=page + 1)))
    if nav:
        rows.append(nav)
    rows.append([(tr("BTN_NEW_GAME"), Menu(action="submit"))])
    rows.append([(tr("BTN_MENU"), Menu(action="home"))])
    text = tr("MY_GAMES", page=page + 1, pages=pages) if games else tr("MY_GAMES_EMPTY")
    return text, keyboard(rows)


async def show_games(message: Message, uid: int, page: int, tr: Tr, edit: bool = False):
    if not ready(uid):
        return await message.answer(tr("EXPIRED"))
    text, markup = games_screen(db.recent_games(uid, limit=-1), page, tr, lang_of(uid))
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.callback_query(Menu.filter(F.action == "games"))
async def on_my_games(cb: CallbackQuery, state: FSMContext, tr: Tr):
    await cb.answer()
    await state.clear()
    await show_games(cb.message, cb.from_user.id, 0, tr)


def my_game(uid: int, gid: int):
    """The game, if it exists and uid played in it."""
    g = db.game(gid)
    return g if g and uid in db.game_player_ids(gid) else None


def game_detail_text(g, people: list, tr: Tr, lang: str) -> str:
    """Date (weekday), average level, then "• Name [tag]" per player. people: (name, game_players row)."""
    date, hm = fmt_day(g["start_at"], lang)
    if g["avg_level"] is None:
        avg = tr("GAME_AVG_NONE")
    else:
        avg = tr("GAME_AVG", avg=f"{g['avg_level']:.1f}")
    lines = [f"{'⏳' if r['pending'] else '•'} {html.escape(name)}"
             + (f" {html.escape(r['tag'])}" if r["tag"] else "") for name, r in people]
    return tr("GAME_DETAIL", date=date, time=hm, avg=avg, n=len(people), names="\n".join(lines))


async def show_game(message: Message, bot: Bot, uid: int, gid: int, tr: Tr, lang: str):
    """One game: date, time and every player, with [Add players] and [« My games]."""
    g = db.game(gid)
    rows = db.game_players(gid)
    names = await asyncio.gather(*(name_of(bot, r["player_id"], lang) for r in rows))
    text = game_detail_text(g, sorted(zip(names, rows), key=lambda p: p[0].casefold()), tr, lang)
    if g["pending"]:
        text += "\n\n" + tr("PENDING_NOTE")
    if g["n"] < MIN_COUNTING_PLAYERS:
        text += "\n\n" + tr("GAME_NOT_COUNTING", min=MIN_COUNTING_PLAYERS)
    await message.edit_text(text, reply_markup=keyboard([
        [(tr("BTN_ADD_PLAYERS"), MyGames(kind="add", value=gid))],
        [(tr("BTN_BACK_GAMES"), MyGames(kind="list", value=0))]]))


@router.callback_query(MyGames.filter())
async def on_my_games_nav(cb: CallbackQuery, callback_data: MyGames, state: FSMContext, bot: Bot, tr: Tr,
                          lang: str):
    uid, kind, value = cb.from_user.id, callback_data.kind, callback_data.value
    if kind == "list":
        await cb.answer()
        await state.clear()
        return await show_games(cb.message, uid, value, tr, edit=True)
    if not ready(uid) or my_game(uid, value) is None:
        return await cb.answer(tr("NOT_IN_GAME"), show_alert=True)
    await cb.answer()
    if kind == "open":
        await state.clear()
        return await show_game(cb.message, bot, uid, value, tr, lang)
    # add: the player picker, without the players already in the game
    await state.set_state(Flow.game_players)
    await state.set_data({"start": db.game(value)["start_at"], "selected": [], "page": 0, "game": value})
    await render_picker(cb.message, state, bot, uid, tr, lang, edit=True)


# --- submit a new game (README §6) -------------------------------------------------
@router.callback_query(Menu.filter(F.action == "submit"))
async def on_submit(cb: CallbackQuery, state: FSMContext, tr: Tr):
    await cb.answer()
    if not ready(cb.from_user.id):
        return await cb.message.answer(tr("EXPIRED"))
    await state.set_state(Flow.game_time)
    await cb.message.answer(tr("ASK_GAME_TIME"))


@router.message(Flow.game_time, F.text)
async def on_game_time(message: Message, state: FSMContext, bot: Bot, tr: Tr, lang: str):
    start = parse_game_time(message.text, now())
    if start is None:
        return await message.answer(tr("BAD_TIME"))
    if start > now():
        return await message.answer(tr("FUTURE_TIME"))
    await state.set_state(Flow.game_players)
    await state.set_data({"start": iso(start), "selected": [message.from_user.id], "page": 0})
    await render_picker(message, state, bot, message.from_user.id, tr, lang)


async def roster(bot: Bot, me: int, lang: str) -> list[tuple[int, str]]:
    """Every consented player with a live name: me first, then alphabetical."""
    ids = [p["telegram_id"] for p in db.all_players()]
    names = await asyncio.gather(*(name_of(bot, i, lang) for i in ids))
    return sorted(zip(ids, names), key=lambda p: (p[0] != me, p[1].casefold()))


async def render_picker(message: Message, state: FSMContext, bot: Bot, me: int, tr: Tr, lang: str,
                        edit: bool = False):
    data = await state.get_data()
    people = await roster(bot, me, lang)
    if data.get("game"):   # adding to an existing game: only people who aren't in it yet
        already = set(db.game_player_ids(data["game"]))
        people = [p for p in people if p[0] not in already]
    selected = set(data["selected"])
    pages = max(1, -(-len(people) // PAGE_SIZE))
    page = min(data["page"], pages - 1)
    rows = [[(("✅ " if pid in selected else "▫️ ") + name, GamePick(kind="toggle", value=pid))]
            for pid, name in people[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]]
    nav = []
    if page > 0:
        nav.append(("◀", GamePick(kind="page", value=page - 1)))
    if page < pages - 1:
        nav.append(("▶", GamePick(kind="page", value=page + 1)))
    if nav:
        rows.append(nav)
    rows.append([(tr("BTN_DONE", n=len(selected)), GamePick(kind="done", value=0))])
    rows.append([(tr("BTN_CANCEL"), GamePick(kind="cancel", value=0))])
    date, hm = fmt_day(data["start"], lang)
    if data.get("game"):
        text = (tr("PICK_ADD", date=date, time=hm, page=page + 1, pages=pages) if people else
                tr("NO_ONE_TO_ADD"))
        if not people:
            rows = rows[-1:]   # only [Cancel]
    else:
        text = tr("PICK_PLAYERS", date=date, time=hm, min=MIN_SUBMIT_PLAYERS, page=page + 1, pages=pages)
    text += "\n\n" + tr("CONTACT_HINT")
    if edit:
        await message.edit_text(text, reply_markup=keyboard(rows))
        await state.update_data(picker=message.message_id)
    else:
        sent = await message.answer(text, reply_markup=keyboard(rows))
        await state.update_data(picker=sent.message_id)


@router.message(Flow.game_players, F.contact)
async def on_contact(message: Message, state: FSMContext, bot: Bot, tr: Tr, lang: str):
    """A shared contact ticks that player in the picker. Only contact.user_id is read; nothing about the
    contact (phone, name) is stored or logged."""
    contact, data = message.contact, await state.get_data()
    selected: list[int] = data["selected"]
    pid = contact.user_id
    if pid is None:
        await message.answer(tr("CONTACT_NO_ACCOUNT"))
    elif db.player(pid) is None:
        link = f"https://t.me/{(await bot.me()).username}"
        await message.answer(tr("CONTACT_NOT_JOINED", name=html.escape(contact.first_name or tr("PLAYER")),
                                link=link), disable_web_page_preview=True)
    elif pid in selected or (data.get("game") and pid in db.game_player_ids(data["game"])):
        await message.answer(tr("CONTACT_ALREADY", name=html.escape(await name_of(bot, pid, lang))))
    elif len(selected) < MAX_PLAYERS:
        selected.append(pid)
        await state.update_data(selected=selected)
        await message.answer(tr("CONTACT_ADDED", name=html.escape(await name_of(bot, pid, lang))))
    if data.get("picker"):   # move the picker below the contact so it stays in view
        with suppress(TelegramBadRequest):
            await bot.edit_message_reply_markup(chat_id=message.chat.id, message_id=data["picker"],
                                                reply_markup=None)
    await render_picker(message, state, bot, message.from_user.id, tr, lang)


@router.callback_query(Flow.game_players, GamePick.filter())
async def on_game_pick(cb: CallbackQuery, callback_data: GamePick, state: FSMContext, bot: Bot, tr: Tr,
                       lang: str):
    uid, kind, value = cb.from_user.id, callback_data.kind, callback_data.value
    data = await state.get_data()
    selected: list[int] = data["selected"]
    gid = data.get("game")   # set when adding players to an existing game from My games
    if kind == "done" and gid and not selected:
        return await cb.answer(tr("NEED_ONE"), show_alert=True)
    if kind == "done" and not gid and (len(selected) < MIN_SUBMIT_PLAYERS or uid not in selected):
        return await cb.answer(tr("NEED_MIN", min=MIN_SUBMIT_PLAYERS), show_alert=True)
    await cb.answer()

    if kind in ("toggle", "page"):
        if kind == "toggle":
            if value in selected:
                selected.remove(value)
            elif len(selected) < MAX_PLAYERS:
                selected.append(value)
            await state.update_data(selected=selected)
        else:
            await state.update_data(page=value)
        return await render_picker(cb.message, state, bot, uid, tr, lang, edit=True)

    if kind == "cancel":
        await state.clear()
        if gid and my_game(uid, gid):
            return await show_game(cb.message, bot, uid, gid, tr, lang)
        await cb.message.edit_text(tr("CANCELLED"))
        return await show_games(cb.message, uid, 0, tr)

    if kind == "done" and gid:
        await state.clear()
        await cb.message.edit_reply_markup(reply_markup=None)
        if my_game(uid, gid) is None:
            return await cb.message.answer(tr("NOT_IN_GAME"), reply_markup=home_kb(tr))
        return await report_saved(cb.message, bot, uid, gid, db.add_players(gid, selected, uid), tr, lang)

    start = parse(data["start"])
    if kind == "done":
        await cb.message.edit_reply_markup(reply_markup=None)
        game = db.matching_game(start, selected)
        if game is None:
            await state.clear()
            gid, res = db.create_game(start, uid, selected)
            return await report_saved(cb.message, bot, uid, gid, res, tr, lang)
        date, hm = fmt_local(game["start_at"])
        existing = db.game_player_ids(game["id"])
        new = [p for p in selected if p not in existing]
        if not new:
            await state.clear()
            return await cb.message.answer(tr("ALL_ALREADY_IN", date=date, time=hm), reply_markup=keyboard(
                [[(tr("BTN_BACK_GAMES"), MyGames(kind="list", value=0))]]))
        await cb.message.answer(
            tr("GAME_EXISTS", date=date, time=hm, existing=await names_list(bot, existing, lang),
               new=await names_list(bot, new, lang)),
            reply_markup=keyboard([[(tr("BTN_SUBMIT_PARTICIPATION"), GamePick(kind="add", value=game["id"])),
                                    (tr("BTN_CANCEL"), GamePick(kind="cancel", value=0))]]))
    elif kind == "add":
        await state.clear()
        await cb.message.edit_reply_markup(reply_markup=None)
        res = db.add_players(value, selected, uid)
        if uid in selected:
            db.confirm(value, uid)   # submitting my own participation confirms it
        await report_saved(cb.message, bot, uid, value, res, tr, lang)


async def report_saved(message: Message, bot: Bot, adder: int, gid: int, res: Added, tr: Tr, lang: str):
    g = db.game(gid)
    date, hm = fmt_local(g["start_at"])
    others = [p for p in res.added if p != adder]
    lines = [tr("GAME_SAVED", date=date, time=hm, n=g["n"] + g["pending"])]
    if others:
        lines.append(tr("CONFIRM_ASKED", hours=int(CONFIRM_WITHIN.total_seconds() // 3600)))
    if res.clash:
        lines.append(tr("SKIPPED_CLASH", names=await names_list(bot, res.clash, lang)))
    if res.full:
        lines.append(tr("SKIPPED_FULL", names=await names_list(bot, res.full, lang)))
    if g["n"] < MIN_COUNTING_PLAYERS:
        lines.append(tr("GAME_NOT_COUNTING", min=MIN_COUNTING_PLAYERS))
    await message.answer("\n\n".join(lines), reply_markup=keyboard([
        [(tr("BTN_RATE_PLAYERS"), Menu(action="rate"))],
        [(tr("BTN_BACK_GAMES"), MyGames(kind="list", value=0))]]))
    await ask_confirmations(message.bot, gid, adder, others)


# --- confirming participation (README §6) ------------------------------------------
def lang_of(uid: int) -> str:
    p = db.player(uid)
    return T.norm(p["lang"]) if p else T.DEFAULT


async def ask_confirmations(bot: Bot, gid: int, adder: int, pids: list[int]) -> None:
    """Ask every player someone else added whether they really played."""
    g = db.game(gid)
    hours = int(CONFIRM_WITHIN.total_seconds() // 3600)
    for pid in pids:
        lang = lang_of(pid)
        tr = partial(T.t, lang)
        date, hm = fmt_day(g["start_at"], lang)
        try:
            await bot.send_message(pid, tr("CONFIRM_ASK", adder=html.escape(await name_of(bot, adder, lang)),
                                           date=date, time=hm, hours=hours),
                                   reply_markup=keyboard([[(tr("BTN_CONFIRM_YES"), Confirm(gid=gid, yes=1)),
                                                           (tr("BTN_CONFIRM_NO"), Confirm(gid=gid, yes=0))]]))
        except TelegramAPIError as e:   # e.g. blocked the bot: stays pending and expires
            log.warning("confirmation request to %s failed: %s", pid, e)


def admin_notice_text(lang: str, gid: int, start_at: str, player: str, player_id: int, adder: str,
                      adder_id: int | None, said_no: bool) -> str:
    tr = partial(T.t, lang)
    date, hm = fmt_local(start_at)
    reason = tr("REASON_SAID_NO") if said_no else tr("REASON_NO_ANSWER",
                                                   hours=int(CONFIRM_WITHIN.total_seconds() // 3600))
    return tr("ADMIN_NOT_CONFIRMED", gid=gid, date=date, time=hm, player=html.escape(player), player_id=player_id,
              adder=html.escape(adder), adder_id=adder_id if adder_id is not None else "–", reason=reason)


async def notify_admin(bot: Bot, row, said_no: bool) -> None:
    """Tell the bot admin a player was removed from a game they were added to by someone else."""
    if not ADMIN_ID:
        return
    g = db.game(row["game_id"])
    if g is None:
        return
    lang = lang_of(ADMIN_ID)
    adder_id = row["added_by"]
    player = await name_of(bot, row["player_id"], lang)
    adder = await name_of(bot, adder_id, lang) if adder_id is not None else T.t(lang, "PLAYER")
    try:
        await bot.send_message(ADMIN_ID, admin_notice_text(lang, g["id"], g["start_at"], player, row["player_id"],
                                                           adder, adder_id, said_no))
    except TelegramAPIError as e:
        log.warning("admin notice failed: %s", e)


@router.callback_query(Confirm.filter())
async def on_confirm(cb: CallbackQuery, callback_data: Confirm, bot: Bot, tr: Tr):
    await cb.answer()
    uid, gid = cb.from_user.id, callback_data.gid
    g = db.game(gid)
    when = dict(zip(("date", "time"), fmt_local(g["start_at"]))) if g else {}
    if callback_data.yes:
        ok = db.confirm(gid, uid)
        return await cb.message.edit_text(tr("CONFIRM_THANKS", **when) if ok else tr("CONFIRM_EXPIRED"))
    row = db.remove_pending(gid, uid)
    if row is None:
        return await cb.message.edit_text(tr("CONFIRM_EXPIRED"))
    await cb.message.edit_text(tr("CONFIRM_REMOVED", **when))
    log.info("player %s removed from game %s: said they didn't play", uid, gid)
    await notify_admin(bot, row, said_no=True)


async def expire_confirmations(bot: Bot) -> int:
    """Remove players who didn't confirm in time, tell them and the admin. Returns how many."""
    n = 0
    for row in db.expired_pending(now() - CONFIRM_WITHIN):
        if db.remove_pending(row["game_id"], row["player_id"]) is None:
            continue   # confirmed or removed meanwhile
        n += 1
        g = db.game(row["game_id"])
        lang = lang_of(row["player_id"])
        date, hm = fmt_local(g["start_at"])
        with suppress(TelegramAPIError):
            await bot.send_message(row["player_id"], T.t(lang, "CONFIRM_TIMED_OUT", date=date, time=hm))
        log.info("player %s removed from game %s: no confirmation", row["player_id"], row["game_id"])
        await notify_admin(bot, row, said_no=False)
    return n


async def expiry_loop(bot: Bot) -> None:
    while True:
        await asyncio.sleep(EXPIRY_CHECK_SECONDS)
        try:
            await expire_confirmations(bot)
        except Exception:
            log.exception("confirmation expiry failed")


# --- choosing whom to rate (README §7) -------------------------------------------
@router.callback_query(Menu.filter(F.action == "rate"))
async def on_rate_menu(cb: CallbackQuery, bot: Bot, tr: Tr, lang: str):
    await cb.answer()
    await show_candidates(cb.message, bot, cb.from_user.id, -1, tr, lang)


@router.callback_query(Page.filter())
async def on_page(cb: CallbackQuery, callback_data: Page, bot: Bot, tr: Tr, lang: str):
    await cb.answer()
    await show_candidates(cb.message, bot, cb.from_user.id, callback_data.page, tr, lang, edit=True)


async def show_candidates(message: Message, bot: Bot, uid: int, page: int, tr: Tr, lang: str,
                          edit: bool = False):
    if not ready(uid):
        return await message.answer(tr("EXPIRED"))
    cands = db.rate_candidates(uid, RERATE_AFTER)
    if not cands:
        return await message.answer(tr("NO_CANDIDATES"), reply_markup=home_kb(tr))
    pages = (len(cands) + PAGE_SIZE - 1) // PAGE_SIZE
    if page < 0:
        # first screen: only players who need my rating; recently rated ones are at the end of "Show all"
        shown = [c for c in cands if c["needs"]][:TOP_N]
        text = tr("CANDIDATES_TOP") if shown else tr("ALL_RATED")
    else:
        page = min(page, pages - 1)
        shown = cands[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        text = tr("CANDIDATES_ALL", page=page + 1, pages=pages)
    names = await asyncio.gather(*(name_of(bot, c["pid"], lang) for c in shown))
    rows = [[(n if c["needs"] else f"✓ {n}", Pick(pid=c["pid"]))] for n, c in zip(names, shown)]
    if page < 0:
        if len(cands) > len(shown):
            rows.append([(tr("BTN_SHOW_ALL"), Page(page=0))])
    else:
        nav = []
        if page > 0:
            nav.append(("◀", Page(page=page - 1)))
        if page < pages - 1:
            nav.append(("▶", Page(page=page + 1)))
        if nav:
            rows.append(nav)
    rows.append([(tr("BTN_MENU"), Menu(action="home"))])
    if edit:
        await message.edit_text(text, reply_markup=keyboard(rows))
    else:
        await message.answer(text, reply_markup=keyboard(rows))


def rateable(uid: int, pid: int) -> tuple[int, str | None] | None:
    """(games together, last game) if uid may rate pid, else None."""
    games, last = db.together(uid, pid)
    if not ready(uid) or pid == uid or games == 0 or db.player(pid) is None:
        return None
    return games, last


def rating_header(name: str, games: int, last: str, rated_at: str | None, tr: Tr, lang: str) -> str:
    """Top of the discipline list for a peer rating (README §7)."""
    lines = [tr("RATE_SELECTED", name=html.escape(name)),
             tr("RATE_INFO", games=games, ago=T.ago(lang, (now() - parse(last)).days))]
    lines.append(tr("RATE_AGAIN", ago=T.ago(lang, (now() - parse(rated_at)).days)) if rated_at
                 else tr("RATE_FIRST"))
    return "\n".join(lines + ["*" + tr("NET_NOTE")])


def scores_of(player: int, rater: int) -> list[int | None]:
    row = db.rating(player, rater)
    return [row[d] if row else None for d in DISCIPLINES]


def disciplines_screen(pid: int, header: str, name: str, scores: list, is_self: bool, tr: Tr,
                       lang: str) -> tuple[str, InlineKeyboardMarkup]:
    """Header, "Choose the discipline…", then one button per discipline with its current score."""
    choose = tr("RATE_CHOOSE_SELF") if is_self else tr("RATE_CHOOSE", name=html.escape(name))
    rows = [[((f"✅{T.discipline_short(lang, d)} - {v}" if v is not None
               else f"❓{T.discipline_short(lang, d)} »"), RateOpen(pid=pid, d=i))]
            for i, (d, v) in enumerate(zip(DISCIPLINES, scores))]
    if not is_self:
        rows.append([(tr("BTN_BACK_TO_MENU"), Menu(action="home"))])
    elif any(v is not None for v in scores):
        rows.append([(tr("BTN_SELF_DONE"), Menu(action="selfdone"))])
    return f"{header}\n\n{choose}", keyboard(rows)


def level_screen(pid: int, d: int, name: str, current: int | None, is_self: bool, tr: Tr,
                 lang: str) -> tuple[str, InlineKeyboardMarkup]:
    """All 7 levels of one discipline in one text block; [0]..[6] save a level (✓ = current one)."""
    key = DISCIPLINES[d]
    title = tr("RATE_SKILL_SELF") if is_self else tr("RATE_SKILL", name=html.escape(name))
    levels = "\n".join(f"<b>•{n}•</b> {html.escape(T.criteria(lang, n, key), quote=False)}"
                       for n in range(MIN_LEVEL, MAX_LEVEL + 1))
    text = f"{title}\n<b>{html.escape(T.discipline(lang, key))}:</b>\n{levels}"
    rows = [[(f"{n}✓" if n == current else str(n), RateSet(pid=pid, d=d, v=n))
             for n in range(MIN_LEVEL, MAX_LEVEL + 1)]]
    if current is None:
        rows.append([(tr("BTN_KEEP_UNRATED"), RateSet(pid=pid, d=d, v=KEEP))])
    else:
        rows.append([(tr("BTN_ERASE", n=current, discipline=T.discipline(lang, key)),
                      RateSet(pid=pid, d=d, v=ERASE))])
    return text, keyboard(rows)


async def edit(message: Message, text: str, markup: InlineKeyboardMarkup) -> None:
    try:
        await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            raise


async def show_disciplines(message: Message, bot: Bot, uid: int, pid: int, tr: Tr, lang: str,
                           new: bool = False) -> bool:
    """The discipline list for rating pid (pid == uid: self-rating). False if uid may not rate pid."""
    if pid == uid:
        if db.player(uid) is None:
            return False
        header, name = tr("SELF_RATE_INTRO"), ""
    else:
        ok = rateable(uid, pid)
        if ok is None:
            return False
        name = with_handle(await name_of(bot, pid, lang))
        header = rating_header(name, *ok, db.rated_at(pid, uid), tr, lang)
    text, markup = disciplines_screen(pid, header, name, scores_of(pid, uid), pid == uid, tr, lang)
    if new:
        await message.answer(text, reply_markup=markup)
    else:
        await edit(message, text, markup)
    return True


def may_rate(uid: int, pid: int) -> bool:
    return db.player(uid) is not None if pid == uid else rateable(uid, pid) is not None


@router.callback_query(Pick.filter())
async def on_pick(cb: CallbackQuery, callback_data: Pick, bot: Bot, tr: Tr, lang: str):
    await cb.answer()
    if not await show_disciplines(cb.message, bot, cb.from_user.id, callback_data.pid, tr, lang):
        await cb.message.answer(tr("NOT_RATEABLE"), reply_markup=home_kb(tr))


@router.callback_query(RateOpen.filter())
async def on_rate_open(cb: CallbackQuery, callback_data: RateOpen, bot: Bot, tr: Tr, lang: str):
    uid, pid, d = cb.from_user.id, callback_data.pid, callback_data.d
    if not may_rate(uid, pid) or not 0 <= d < len(DISCIPLINES):
        return await cb.answer(tr("NOT_RATEABLE"), show_alert=True)
    await cb.answer()
    name = "" if pid == uid else with_handle(await name_of(bot, pid, lang))
    await edit(cb.message, *level_screen(pid, d, name, scores_of(pid, uid)[d], pid == uid, tr, lang))


@router.callback_query(RateSet.filter())
async def on_rate_set(cb: CallbackQuery, callback_data: RateSet, bot: Bot, tr: Tr, lang: str):
    """A level (save it), Erase (NULL) or Keep unrated; then back to the discipline list."""
    uid, pid, d, v = cb.from_user.id, callback_data.pid, callback_data.d, callback_data.v
    if not may_rate(uid, pid) or not 0 <= d < len(DISCIPLINES):
        return await cb.answer(tr("NOT_RATEABLE"), show_alert=True)
    key = DISCIPLINES[d]
    current = scores_of(pid, uid)[d]
    if MIN_LEVEL <= v <= MAX_LEVEL and v != current:
        db.set_score(pid, uid, key, v)
        await cb.answer(tr("SCORE_SAVED", discipline=T.discipline(lang, key), n=v))
    elif v == ERASE and current is not None:
        db.set_score(pid, uid, key, None)
        await cb.answer(tr("SCORE_ERASED", discipline=T.discipline(lang, key)))
    else:
        await cb.answer()
    await show_disciplines(cb.message, bot, uid, pid, tr, lang)


@router.callback_query(Menu.filter(F.action == "selfdone"))
async def on_self_done(cb: CallbackQuery, bot: Bot, tr: Tr, lang: str):
    uid = cb.from_user.id
    if db.player(uid) is None:
        return await cb.answer(tr("EXPIRED"), show_alert=True)
    if not db.is_self_rated(uid):
        return await cb.answer(tr("SELF_RATE_REQUIRED"), show_alert=True)
    await cb.answer()
    with suppress(TelegramBadRequest):
        await cb.message.edit_reply_markup(reply_markup=None)
    r = live_levels()[uid]
    if db.player(uid)["level"] is None:       # first rating: show it in games before the nightly run
        db.set_level(uid, r.level, r.tag)
        db.refresh_avg()
    shown = await nightly.set_tag(bot, GROUP_ID, uid, r.tag)
    if shown:
        db.set_tag_shown(uid, r.tag)
    await cb.message.answer(tr("SELF_RATE_DONE" if shown else "SELF_RATE_DONE_NO_TAG", tag=html.escape(r.tag)))
    await show_menu(cb.message, uid, tr)


# --- my stats (README §10) -------------------------------------------------------
@router.callback_query(Menu.filter(F.action == "stats"))
async def on_stats(cb: CallbackQuery, tr: Tr, lang: str):
    await cb.answer()
    uid = cb.from_user.id
    if not ready(uid) or db.peer_count(uid) < STATS_MIN_PEERS:
        return await cb.message.answer(tr("STATS_LOCKED", n=STATS_MIN_PEERS), reply_markup=home_kb(tr))
    r = live_levels()[uid]
    disc = "\n".join(f"{T.discipline(lang, k)}: " + (f"{r.disciplines[k]:.1f}" if k in r.disciplines else "–")
                     for k in DISCIPLINES)
    games = "\n".join(tr("STATS_GAME", date=d, time=hm, n=g["n"])
                      for g in db.recent_games(uid) for d, hm in [fmt_local(g["start_at"])]) or "–"
    await cb.message.answer(tr("STATS", level=f"{r.level:.1f}", tag=html.escape(r.tag), disciplines=disc,
                               received=db.peer_count(uid), given=db.given_count(uid), games=games),
                            reply_markup=home_kb(tr))


# --- revoke (README §11) ---------------------------------------------------------
@router.callback_query(Menu.filter(F.action == "revoke"))
async def on_revoke(cb: CallbackQuery, tr: Tr):
    await cb.answer()
    await cb.message.answer(tr("REVOKE_ASK"), reply_markup=keyboard([[
        (tr("BTN_REVOKE_YES"), Menu(action="revoke_yes")), (tr("BTN_CANCEL"), Menu(action="home"))]]))


@router.callback_query(Menu.filter(F.action == "revoke_yes"))
async def on_revoke_yes(cb: CallbackQuery, state: FSMContext, bot: Bot, tr: Tr):
    await cb.answer()
    await state.clear()
    await cb.message.edit_reply_markup(reply_markup=None)
    await forget_user(bot, cb.from_user.id)
    await cb.message.answer(tr("REVOKED"))


@router.chat_member(ChatMemberUpdatedFilter(member_status_changed=LEAVE_TRANSITION))
async def on_left_group(event: ChatMemberUpdated, bot: Bot):
    if event.chat.id == GROUP_ID:
        await forget_user(bot, event.new_chat_member.user.id)


# --- admin (README §12) ----------------------------------------------------------
@router.message(Command("export"))
async def cmd_export(message: Message):
    if message.from_user.id != ADMIN_ID:
        return  # no response for anyone else
    stamp = now().strftime("%Y%m%d-%H%M")
    await message.answer_document(BufferedInputFile(nightly.export_zip(db), f"vball-export-{stamp}.zip"))


# --- fallbacks -------------------------------------------------------------------
@router.callback_query(Menu.filter(F.action == "home"))
async def on_home(cb: CallbackQuery, state: FSMContext, tr: Tr):
    await cb.answer()
    await state.clear()
    if ready(cb.from_user.id):
        await show_menu(cb.message, cb.from_user.id, tr)
    else:
        await cb.message.answer(tr("EXPIRED"))


@router.callback_query()
async def on_stale(cb: CallbackQuery, tr: Tr):
    await cb.answer(tr("EXPIRED"), show_alert=True)


@router.message()
async def on_other(message: Message, state: FSMContext, tr: Tr):
    uid = message.from_user.id
    if ready(uid):
        await state.clear()
        await show_menu(message, uid, tr)
    else:
        await message.answer(tr("START_HINT"))


# --- entry point -----------------------------------------------------------------
async def set_commands(bot: Bot) -> None:
    """The /start description in each language (English is the default for everyone else)."""
    for lang in T.LANGS:
        try:
            await bot.set_my_commands([BotCommand(command="start", description=T.t(lang, "CMD_START"))],
                                      language_code=None if lang == T.DEFAULT else lang)
        except TelegramAPIError as e:
            log.warning("setMyCommands(%s) failed: %s", lang, e)


async def main():
    global db
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not (BOT_TOKEN and GROUP_ID and ADMIN_ID):
        raise SystemExit("BOT_TOKEN, GROUP_ID and ADMIN_ID must be set (see .env.example)")
    db = DB(DB_PATH)
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.message.outer_middleware(MembersOnly())
    dp.callback_query.outer_middleware(MembersOnly())
    dp.include_router(router)
    await set_commands(bot)
    asyncio.create_task(nightly.loop(bot, db, GROUP_ID))
    asyncio.create_task(expiry_loop(bot))
    log.info("starting polling as @%s", (await bot.me()).username)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
