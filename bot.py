"""Volleyball rating bot: handlers, menus and conversation flows (README §4–§12)."""
from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import time
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
                           InlineKeyboardMarkup, InputRichBlockFooter, InputRichBlockParagraph,
                           InputRichBlockSectionHeading, InputRichMessage, Message)

import nightly
import texts as T
from db import DB, MAX_PLAYERS, MIN_COUNTING_PLAYERS, Added, iso, now, parse
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
MEMBER_CACHE_SECONDS = 300
FIRST_LEVEL = 0                    # level shown first on the rating screen

log = logging.getLogger("bot")
db: DB  # set in main()
router = Router()
Tr = Callable[..., str]            # tr("KEY", **kw) -> text in the user's language


# --- callback data ---------------------------------------------------------------
class Menu(CallbackData, prefix="m"):
    action: str        # home, submit, rate, stats, language, revoke, revoke_yes, consent, decline


class Lang(CallbackData, prefix="l"):
    code: str


class GamePick(CallbackData, prefix="g"):
    kind: str          # toggle (value = player id), page, done, add (value = game id), cancel
    value: int


class Page(CallbackData, prefix="p"):
    page: int          # -1 = top 5


class Pick(CallbackData, prefix="r"):
    pid: int


class Score(CallbackData, prefix="s"):
    step: int          # discipline index the button belongs to (stale presses are ignored)
    kind: str          # show (browse to level), choose, skip
    value: int


class Flow(StatesGroup):
    game_time = State()
    game_players = State()
    rating = State()


# --- helpers ---------------------------------------------------------------------
def fmt_local(start_at: str) -> tuple[str, str]:
    t = parse(start_at).astimezone(TZ)
    return t.strftime("%d.%m"), t.strftime("%H:%M")


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
    rows = [[(tr("BTN_SUBMIT"), Menu(action="submit"))], [(tr("BTN_RATE"), Menu(action="rate"))]]
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
    await message.answer(tr("SELF_RATE_INTRO"))
    await begin_rating(message, state, lang, target=uid, is_self=True, header=[], name="")


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
    date, hm = fmt_local(data["start"])
    text = tr("PICK_PLAYERS", date=date, time=hm, min=MIN_COUNTING_PLAYERS, page=page + 1, pages=pages)
    if edit:
        await message.edit_text(text, reply_markup=keyboard(rows))
    else:
        await message.answer(text, reply_markup=keyboard(rows))


@router.callback_query(Flow.game_players, GamePick.filter())
async def on_game_pick(cb: CallbackQuery, callback_data: GamePick, state: FSMContext, bot: Bot, tr: Tr,
                       lang: str):
    uid, kind, value = cb.from_user.id, callback_data.kind, callback_data.value
    data = await state.get_data()
    selected: list[int] = data["selected"]
    if kind == "done" and len(selected) < MIN_COUNTING_PLAYERS:
        return await cb.answer(tr("NEED_MIN", min=MIN_COUNTING_PLAYERS), show_alert=True)
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
        await cb.message.edit_text(tr("CANCELLED"))
        return await show_menu(cb.message, uid, tr)

    start = parse(data["start"])
    if kind == "done":
        await cb.message.edit_reply_markup(reply_markup=None)
        game = db.matching_game(start, selected)
        if game is None:
            await state.clear()
            gid, res = db.create_game(start, uid, selected)
            return await report_saved(cb.message, bot, gid, res, tr, lang)
        date, hm = fmt_local(game["start_at"])
        existing = db.game_player_ids(game["id"])
        new = [p for p in selected if p not in existing]
        if not new:
            await state.clear()
            return await cb.message.answer(tr("ALL_ALREADY_IN", date=date, time=hm), reply_markup=menu_kb(uid, tr))
        await cb.message.answer(
            tr("GAME_EXISTS", date=date, time=hm, existing=await names_list(bot, existing, lang),
               new=await names_list(bot, new, lang)),
            reply_markup=keyboard([[(tr("BTN_SUBMIT_PARTICIPATION"), GamePick(kind="add", value=game["id"])),
                                    (tr("BTN_CANCEL"), GamePick(kind="cancel", value=0))]]))
    elif kind == "add":
        await state.clear()
        await cb.message.edit_reply_markup(reply_markup=None)
        await report_saved(cb.message, bot, value, db.add_players(value, selected), tr, lang)


async def report_saved(message: Message, bot: Bot, gid: int, res: Added, tr: Tr, lang: str):
    g = db.game(gid)
    date, hm = fmt_local(g["start_at"])
    lines = [tr("GAME_SAVED", date=date, time=hm, n=g["n"])]
    if res.clash:
        lines.append(tr("SKIPPED_CLASH", names=await names_list(bot, res.clash, lang)))
    if res.full:
        lines.append(tr("SKIPPED_FULL", names=await names_list(bot, res.full, lang)))
    await message.answer("\n\n".join(lines), reply_markup=keyboard([
        [(tr("BTN_RATE_PLAYERS"), Menu(action="rate"))], [(tr("BTN_MENU"), Menu(action="home"))]]))


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
        shown, text = cands[:TOP_N], tr("CANDIDATES_TOP")
    else:
        page = min(page, pages - 1)
        shown = cands[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        text = tr("CANDIDATES_ALL", page=page + 1, pages=pages)
    names = await asyncio.gather(*(name_of(bot, c["pid"], lang) for c in shown))
    rows = [[(n, Pick(pid=c["pid"]))] for n, c in zip(names, shown)]
    if page < 0:
        if len(cands) > TOP_N:
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


@router.callback_query(Pick.filter())
async def on_pick(cb: CallbackQuery, callback_data: Pick, state: FSMContext, bot: Bot, tr: Tr, lang: str):
    await cb.answer()
    uid, pid = cb.from_user.id, callback_data.pid
    games, last = db.together(uid, pid)
    if not ready(uid) or pid == uid or games == 0 or db.player(pid) is None:
        return await cb.message.answer(tr("NOT_RATEABLE"), reply_markup=home_kb(tr))
    name = await name_of(bot, pid, lang)
    header = [tr("RATING_HEADER", name=name),
              tr("RATING_INFO", games=games, ago=T.ago(lang, (now() - parse(last)).days))]
    await begin_rating(cb.message, state, lang, target=pid, is_self=False, header=header, name=name)


# --- the rating screen (README §7) -----------------------------------------------
async def begin_rating(message: Message, state: FSMContext, lang: str, target: int, is_self: bool,
                       header: list[str], name: str):
    await state.set_state(Flow.rating)
    await state.set_data({"target": target, "self": is_self, "name": name, "header": header,
                          "scores": [], "level": FIRST_LEVEL, "msg": None})
    await render_discipline(message, state, lang)


def discipline_screen(data: dict, lang: str) -> tuple[InputRichMessage, InlineKeyboardMarkup]:
    """One discipline, one level's criteria. ◀ / ▶ browse levels, ✔ saves the shown level."""
    tr = partial(T.t, lang)
    step = len(data["scores"])
    key = DISCIPLINES[step]
    level = data["level"]
    question = tr("WHICH_LEVEL_SELF") if data["self"] else tr("WHICH_LEVEL", name=data["name"])
    blocks = [InputRichBlockSectionHeading(text=line, size=3 if i else 2) for i, line in enumerate(data["header"])]
    blocks += [
        InputRichBlockSectionHeading(text=tr("DISCIPLINE_STEP", discipline=T.discipline(lang, key), step=step + 1),
                                     size=2),
        InputRichBlockSectionHeading(text=tr("LEVEL", n=level), size=3),
        InputRichBlockParagraph(text=T.criteria(lang, level, key)),
        InputRichBlockParagraph(text=question),
        InputRichBlockFooter(text=tr("NET_NOTE")),
    ]
    row = []
    if level > MIN_LEVEL:
        row.append(("◀", Score(step=step, kind="show", value=level - 1)))
    row.append((tr("BTN_CHOOSE", n=level), Score(step=step, kind="choose", value=level)))
    if level < MAX_LEVEL:
        row.append(("▶", Score(step=step, kind="show", value=level + 1)))
    rows = [row, [(tr("BTN_CANT_JUDGE"), Score(step=step, kind="skip", value=level))]]
    return InputRichMessage(blocks=blocks), keyboard(rows)


async def render_discipline(message: Message, state: FSMContext, lang: str):
    data = await state.get_data()
    rich, markup = discipline_screen(data, lang)
    bot = message.bot
    if data.get("msg"):
        try:
            await bot.edit_message_text(chat_id=message.chat.id, message_id=data["msg"],
                                        rich_message=rich, reply_markup=markup)
            return
        except TelegramBadRequest as e:
            if "not modified" in str(e):
                return
            log.warning("editing rich message failed, sending a new one: %s", e)
    sent = await bot.send_rich_message(chat_id=message.chat.id, rich_message=rich, reply_markup=markup)
    await state.update_data(msg=sent.message_id)


@router.callback_query(Flow.rating, Score.filter())
async def on_score(cb: CallbackQuery, callback_data: Score, state: FSMContext, bot: Bot, tr: Tr, lang: str):
    await cb.answer()
    data = await state.get_data()
    if callback_data.step != len(data["scores"]) or data.get("msg") != cb.message.message_id:
        return  # stale button
    level = max(MIN_LEVEL, min(MAX_LEVEL, callback_data.value))
    if callback_data.kind == "show":
        await state.update_data(level=level)
        return await render_discipline(cb.message, state, lang)

    # choose: save the level and open the next discipline at it; skip: keep the shown level
    scores = data["scores"] + [level if callback_data.kind == "choose" else None]
    if len(scores) < len(DISCIPLINES):
        await state.update_data(scores=scores, level=level)
        return await render_discipline(cb.message, state, lang)

    await bot.edit_message_reply_markup(chat_id=cb.message.chat.id, message_id=data["msg"], reply_markup=None)
    uid = cb.from_user.id
    if data["self"]:
        await finish_self_rating(cb.message, state, uid, scores, bot, tr, lang)
    else:
        await finish_peer_rating(cb.message, state, uid, data["target"], scores, data["name"], tr)


async def finish_self_rating(message: Message, state: FSMContext, uid: int, scores: list, bot: Bot, tr: Tr,
                             lang: str):
    if not db.save_rating(uid, uid, scores):
        await message.answer(tr("SELF_RATE_REQUIRED"))
        return await begin_rating(message, state, lang, target=uid, is_self=True, header=[], name="")
    r = live_levels()[uid]
    shown = await nightly.set_tag(bot, GROUP_ID, uid, r.tag)
    if shown:
        db.set_tag_shown(uid, r.tag)
    await message.answer(tr("SELF_RATE_DONE" if shown else "SELF_RATE_DONE_NO_TAG", tag=html.escape(r.tag)))
    await state.clear()
    await show_menu(message, uid, tr)


async def finish_peer_rating(message: Message, state: FSMContext, uid: int, target: int, scores: list,
                             name: str, tr: Tr):
    await state.clear()
    if db.player(target) is None or db.together(uid, target)[0] == 0:
        return await message.answer(tr("NOT_RATEABLE"), reply_markup=home_kb(tr))
    if not db.save_rating(target, uid, scores):
        return await message.answer(tr("NOTHING_SAVED"), reply_markup=menu_kb(uid, tr))
    await message.answer(tr("RATING_SAVED", name=html.escape(name)), reply_markup=menu_kb(uid, tr))


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
    log.info("starting polling as @%s", (await bot.me()).username)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
