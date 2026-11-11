"""Volleyball rating bot: handlers, menus and conversation flows (README §4–§12)."""
from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import LEAVE_TRANSITION, ChatMemberUpdatedFilter, Command, CommandStart
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (BufferedInputFile, CallbackQuery, ChatMemberUpdated, InlineKeyboardButton,
                           InlineKeyboardMarkup, InputRichBlockFooter, InputRichBlockParagraph, InputRichBlockSectionHeading,
                           InputRichBlockTable, InputRichMessage, Message, RichBlockTableCell)

import nightly
import texts as T
from db import DB, MAX_PLAYERS, MIN_COUNTING_PLAYERS, Added, iso, now, parse
from rating import DISCIPLINES, MAX_LEVEL, MIN_LEVEL, WINDOW, clamp_window, compute_levels, window_start


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
SELF_START_CENTER = 3

log = logging.getLogger("bot")
db: DB  # set in main()
router = Router()


# --- callback data ---------------------------------------------------------------
class Menu(CallbackData, prefix="m"):
    action: str        # home, submit, rate, stats, revoke, revoke_yes, consent, decline


class GamePick(CallbackData, prefix="g"):
    kind: str          # toggle (value = player id), page, done, add (value = game id), cancel
    value: int


class Page(CallbackData, prefix="p"):
    page: int          # -1 = top 5


class Pick(CallbackData, prefix="r"):
    pid: int


class Score(CallbackData, prefix="s"):
    step: int          # discipline index the button belongs to (stale presses are ignored)
    kind: str          # level, skip, shift
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


async def name_of(bot: Bot, uid: int) -> str:
    """Names are never stored: fetched live, cached in memory for a few minutes."""
    hit = _name_cache.get(uid)
    if hit and time.monotonic() - hit[0] < MEMBER_CACHE_SECONDS:
        return hit[1]
    try:
        async with _lookups:
            m = await bot.get_chat_member(GROUP_ID, uid)
        name = _display(m.user)
    except TelegramAPIError:
        name = "Player"
    _name_cache[uid] = (time.monotonic(), name)
    return name


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
    """Private chats only, and only for members of GROUP_ID (README §4.1)."""

    async def __call__(self, handler, event, data):
        chat, user = data.get("event_chat"), data.get("event_from_user")
        if chat is None or chat.type != "private" or user is None:
            return None
        member = await is_member(data["bot"], user.id)
        if member is False and db.player(user.id):
            await forget_user(data["bot"], user.id)   # missed the "left" update
        if not member:
            if isinstance(event, CallbackQuery):
                await event.answer(T.NOT_MEMBER, show_alert=True)
            else:
                await event.answer(T.NOT_MEMBER)
            return None
        return await handler(event, data)


# --- menu ------------------------------------------------------------------------
def menu_kb(uid: int) -> InlineKeyboardMarkup:
    rows = [[(T.BTN_SUBMIT, Menu(action="submit"))], [(T.BTN_RATE, Menu(action="rate"))]]
    if db.peer_count(uid) >= STATS_MIN_PEERS:
        rows.append([(T.BTN_STATS, Menu(action="stats"))])
    rows.append([(T.BTN_REVOKE, Menu(action="revoke"))])
    return keyboard(rows)


def home_kb() -> InlineKeyboardMarkup:
    return keyboard([[(T.BTN_MENU, Menu(action="home"))]])


async def show_menu(message: Message, uid: int) -> None:
    await message.answer(T.MENU, reply_markup=menu_kb(uid))


def ready(uid: int) -> bool:
    return db.player(uid) is not None and db.is_self_rated(uid)


# --- onboarding (README §4) ------------------------------------------------------
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    uid = message.from_user.id
    await state.clear()
    if db.player(uid) is None:
        await message.answer(T.WELCOME, reply_markup=keyboard([[
            (T.BTN_AGREE, Menu(action="consent")), (T.BTN_DECLINE, Menu(action="decline"))]]))
    elif not db.is_self_rated(uid):
        await start_self_rating(message, state, uid)
    else:
        await show_menu(message, uid)


@router.callback_query(Menu.filter(F.action == "consent"))
async def on_consent(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    db.add_player(cb.from_user.id)
    await cb.message.edit_reply_markup(reply_markup=None)
    if db.is_self_rated(cb.from_user.id):
        await state.clear()
        await show_menu(cb.message, cb.from_user.id)
    else:
        await start_self_rating(cb.message, state, cb.from_user.id)


@router.callback_query(Menu.filter(F.action == "decline"))
async def on_decline(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.clear()
    await cb.message.edit_text(T.DECLINED)


async def start_self_rating(message: Message, state: FSMContext, uid: int):
    await message.answer(T.SELF_RATE_INTRO)
    await begin_rating(message, state, uid, uid, center=SELF_START_CENTER, header=[])


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
async def on_submit(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    if not ready(cb.from_user.id):
        return await cb.message.answer(T.EXPIRED)
    await state.set_state(Flow.game_time)
    await cb.message.answer(T.ASK_GAME_TIME)


@router.message(Flow.game_time, F.text)
async def on_game_time(message: Message, state: FSMContext, bot: Bot):
    start = parse_game_time(message.text, now())
    if start is None:
        return await message.answer(T.BAD_TIME)
    if start > now():
        return await message.answer(T.FUTURE_TIME)
    await state.set_state(Flow.game_players)
    await state.set_data({"start": iso(start), "selected": [message.from_user.id], "page": 0})
    await render_picker(message, state, bot, me=message.from_user.id)


async def roster(bot: Bot, me: int) -> list[tuple[int, str]]:
    """Every consented player with a live name: me first, then alphabetical."""
    ids = [p["telegram_id"] for p in db.all_players()]
    names = await asyncio.gather(*(name_of(bot, i) for i in ids))
    return sorted(zip(ids, names), key=lambda p: (p[0] != me, p[1].casefold()))


async def render_picker(message: Message, state: FSMContext, bot: Bot, me: int, edit: bool = False):
    data = await state.get_data()
    people = await roster(bot, me)
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
    rows.append([(T.BTN_DONE.format(n=len(selected)), GamePick(kind="done", value=0))])
    rows.append([(T.BTN_CANCEL, GamePick(kind="cancel", value=0))])
    date, hm = fmt_local(data["start"])
    text = T.PICK_PLAYERS.format(date=date, time=hm, min=MIN_COUNTING_PLAYERS, page=page + 1, pages=pages)
    if edit:
        await message.edit_text(text, reply_markup=keyboard(rows))
    else:
        await message.answer(text, reply_markup=keyboard(rows))


async def names_list(bot: Bot, uids) -> str:
    return ", ".join(html.escape(n) for n in await asyncio.gather(*(name_of(bot, u) for u in uids)))


@router.callback_query(Flow.game_players, GamePick.filter())
async def on_game_pick(cb: CallbackQuery, callback_data: GamePick, state: FSMContext, bot: Bot):
    uid, kind, value = cb.from_user.id, callback_data.kind, callback_data.value
    data = await state.get_data()
    selected: list[int] = data["selected"]
    if kind == "done" and len(selected) < MIN_COUNTING_PLAYERS:
        return await cb.answer(T.NEED_MIN.format(min=MIN_COUNTING_PLAYERS), show_alert=True)
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
        return await render_picker(cb.message, state, bot, me=uid, edit=True)

    if kind == "cancel":
        await state.clear()
        await cb.message.edit_text(T.CANCELLED)
        return await show_menu(cb.message, uid)

    start = parse(data["start"])
    if kind == "done":
        await cb.message.edit_reply_markup(reply_markup=None)
        game = db.matching_game(start, selected)
        if game is None:
            await state.clear()
            gid, res = db.create_game(start, uid, selected)
            return await report_saved(cb.message, bot, gid, res)
        date, hm = fmt_local(game["start_at"])
        existing = db.game_player_ids(game["id"])
        new = [p for p in selected if p not in existing]
        if not new:
            await state.clear()
            return await cb.message.answer(T.ALL_ALREADY_IN.format(date=date, time=hm), reply_markup=menu_kb(uid))
        await cb.message.answer(
            T.GAME_EXISTS.format(date=date, time=hm, existing=await names_list(bot, existing),
                                 new=await names_list(bot, new)),
            reply_markup=keyboard([[(T.BTN_SUBMIT_PARTICIPATION, GamePick(kind="add", value=game["id"])),
                                    (T.BTN_CANCEL, GamePick(kind="cancel", value=0))]]))
    elif kind == "add":
        await state.clear()
        await cb.message.edit_reply_markup(reply_markup=None)
        await report_saved(cb.message, bot, value, db.add_players(value, selected))


async def report_saved(message: Message, bot: Bot, gid: int, res: Added):
    g = db.game(gid)
    date, hm = fmt_local(g["start_at"])
    lines = [T.GAME_SAVED.format(date=date, time=hm, n=g["n"])]
    if res.clash:
        lines.append(T.SKIPPED_CLASH.format(names=await names_list(bot, res.clash)))
    if res.full:
        lines.append(T.SKIPPED_FULL.format(names=await names_list(bot, res.full)))
    await message.answer("\n\n".join(lines), reply_markup=keyboard([
        [(T.BTN_RATE_PLAYERS, Menu(action="rate"))], [(T.BTN_MENU, Menu(action="home"))]]))


# --- choosing whom to rate (README §7) -------------------------------------------
@router.callback_query(Menu.filter(F.action == "rate"))
async def on_rate_menu(cb: CallbackQuery, bot: Bot):
    await cb.answer()
    await show_candidates(cb.message, bot, cb.from_user.id, page=-1)


@router.callback_query(Page.filter())
async def on_page(cb: CallbackQuery, callback_data: Page, bot: Bot):
    await cb.answer()
    await show_candidates(cb.message, bot, cb.from_user.id, page=callback_data.page, edit=True)


async def show_candidates(message: Message, bot: Bot, uid: int, page: int, edit: bool = False):
    if not ready(uid):
        return await message.answer(T.EXPIRED)
    cands = db.rate_candidates(uid, RERATE_AFTER)
    if not cands:
        return await message.answer(T.NO_CANDIDATES, reply_markup=home_kb())
    pages = (len(cands) + PAGE_SIZE - 1) // PAGE_SIZE
    if page < 0:
        shown, text = cands[:TOP_N], T.CANDIDATES_TOP
    else:
        page = min(page, pages - 1)
        shown = cands[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        text = T.CANDIDATES_ALL.format(page=page + 1, pages=pages)
    names = await asyncio.gather(*(name_of(bot, c["pid"]) for c in shown))
    rows = [[(n, Pick(pid=c["pid"]))] for n, c in zip(names, shown)]
    if page < 0:
        if len(cands) > TOP_N:
            rows.append([(T.BTN_SHOW_ALL, Page(page=0))])
    else:
        nav = []
        if page > 0:
            nav.append(("◀", Page(page=page - 1)))
        if page < pages - 1:
            nav.append(("▶", Page(page=page + 1)))
        if nav:
            rows.append(nav)
    rows.append([(T.BTN_MENU, Menu(action="home"))])
    if edit:
        await message.edit_text(text, reply_markup=keyboard(rows))
    else:
        await message.answer(text, reply_markup=keyboard(rows))


@router.callback_query(Pick.filter())
async def on_pick(cb: CallbackQuery, callback_data: Pick, state: FSMContext, bot: Bot):
    await cb.answer()
    uid, pid = cb.from_user.id, callback_data.pid
    games, last = db.together(uid, pid)
    if not ready(uid) or pid == uid or games == 0 or db.player(pid) is None:
        return await cb.message.answer(T.NOT_RATEABLE, reply_markup=home_kb())
    name = await name_of(bot, pid)
    days = (now() - parse(last)).days
    header = [f"Rating {name}", f"Games together: {games} · last: {T.ago(days)}"]
    stored = db.player(pid)["level"]
    if stored is None:
        r = live_levels().get(pid)
        stored = r.level if r else SELF_START_CENTER
    await begin_rating(cb.message, state, uid, pid, center=stored, header=header, name=name)


# --- the rating screen (README §7) -----------------------------------------------
async def begin_rating(message: Message, state: FSMContext, rater: int, target: int, center: float,
                       header: list[str], name: str = T.SELF_NAME):
    await state.set_state(Flow.rating)
    await state.set_data({"target": target, "name": name, "header": header, "scores": [], "start": window_start(center),
                          "center": center, "msg": None})
    await render_discipline(message, state)


def discipline_screen(data: dict) -> tuple[InputRichMessage, InlineKeyboardMarkup]:
    step = len(data["scores"])
    key = DISCIPLINES[step]
    start = data["start"]
    levels = range(start, start + WINDOW)

    def cell(text, header=False):
        return RichBlockTableCell(align="left", valign="top", text=text, is_header=header or None)

    table = [[cell("Level", True), cell(T.DISCIPLINE_NAMES[key], True)]]
    table += [[cell(str(lv)), cell(T.criteria(lv, key))] for lv in levels]
    blocks = [InputRichBlockSectionHeading(text=line, size=3 if i else 2) for i, line in enumerate(data["header"])]
    blocks += [
        InputRichBlockSectionHeading(text=f"{T.DISCIPLINE_NAMES[key]} ({step + 1}/6)", size=2),
        InputRichBlockTable(cells=table, is_bordered=True, is_striped=True),
        InputRichBlockParagraph(text=f"Which level fits {data['name']} best?"),
        InputRichBlockFooter(text=T.NET_NOTE),
    ]
    rows = [[(str(lv), Score(step=step, kind="level", value=lv)) for lv in levels]]
    nav = []
    if start > MIN_LEVEL:
        nav.append(("◀", Score(step=step, kind="shift", value=start - 1)))
    nav.append((T.BTN_CANT_JUDGE, Score(step=step, kind="skip", value=0)))
    if start + WINDOW - 1 < MAX_LEVEL:
        nav.append(("▶", Score(step=step, kind="shift", value=start + 1)))
    rows.append(nav)
    return InputRichMessage(blocks=blocks), keyboard(rows)


async def render_discipline(message: Message, state: FSMContext):
    data = await state.get_data()
    rich, markup = discipline_screen(data)
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
async def on_score(cb: CallbackQuery, callback_data: Score, state: FSMContext, bot: Bot):
    await cb.answer()
    data = await state.get_data()
    if callback_data.step != len(data["scores"]) or data.get("msg") != cb.message.message_id:
        return  # stale button
    if callback_data.kind == "shift":
        await state.update_data(start=clamp_window(callback_data.value))
        return await render_discipline(cb.message, state)

    value = callback_data.value if callback_data.kind == "level" else None
    scores = data["scores"] + [value]
    uid, target = cb.from_user.id, data["target"]
    center = data["center"]
    if target == uid and value is not None:
        center = value  # self-rating: next discipline centers on the previous answer
    if len(scores) < len(DISCIPLINES):
        await state.update_data(scores=scores, center=center, start=window_start(center))
        return await render_discipline(cb.message, state)

    await bot.edit_message_reply_markup(chat_id=cb.message.chat.id, message_id=data["msg"], reply_markup=None)
    if target == uid:
        await finish_self_rating(cb.message, state, uid, scores, bot)
    else:
        await finish_peer_rating(cb.message, state, uid, target, scores, data["name"])


async def finish_self_rating(message: Message, state: FSMContext, uid: int, scores: list, bot: Bot):
    if not db.save_rating(uid, uid, scores):
        await message.answer(T.SELF_RATE_REQUIRED)
        return await begin_rating(message, state, uid, uid, center=SELF_START_CENTER, header=[])
    r = live_levels()[uid]
    shown = await nightly.set_tag(bot, GROUP_ID, uid, r.tag)
    if shown:
        db.set_tag_shown(uid, r.tag)
    await message.answer((T.SELF_RATE_DONE if shown else T.SELF_RATE_DONE_NO_TAG).format(tag=html.escape(r.tag)))
    await state.clear()
    await show_menu(message, uid)


async def finish_peer_rating(message: Message, state: FSMContext, uid: int, target: int, scores: list,
                             name: str):
    await state.clear()
    if db.player(target) is None or db.together(uid, target)[0] == 0:
        return await message.answer(T.NOT_RATEABLE, reply_markup=home_kb())
    if not db.save_rating(target, uid, scores):
        return await message.answer(T.NOTHING_SAVED, reply_markup=menu_kb(uid))
    await message.answer(T.RATING_SAVED.format(name=html.escape(name)), reply_markup=menu_kb(uid))


# --- my stats (README §10) -------------------------------------------------------
@router.callback_query(Menu.filter(F.action == "stats"))
async def on_stats(cb: CallbackQuery):
    await cb.answer()
    uid = cb.from_user.id
    if not ready(uid) or db.peer_count(uid) < STATS_MIN_PEERS:
        return await cb.message.answer(T.STATS_LOCKED.format(n=STATS_MIN_PEERS), reply_markup=home_kb())
    r = live_levels()[uid]
    disc = "\n".join(f"{T.DISCIPLINE_NAMES[k]}: {r.disciplines[k]:.1f}" if k in r.disciplines
                     else f"{T.DISCIPLINE_NAMES[k]}: –" for k in DISCIPLINES)
    games = "\n".join(f"{' '.join(fmt_local(g['start_at']))} · {g['n']} players"
                      for g in db.recent_games(uid)) or "–"
    await cb.message.answer(T.STATS.format(level=r.level, tag=html.escape(r.tag), disciplines=disc,
                                           received=db.peer_count(uid), given=db.given_count(uid),
                                           games=games), reply_markup=home_kb())


# --- revoke (README §11) ---------------------------------------------------------
@router.callback_query(Menu.filter(F.action == "revoke"))
async def on_revoke(cb: CallbackQuery):
    await cb.answer()
    await cb.message.answer(T.REVOKE_ASK, reply_markup=keyboard([[
        (T.BTN_REVOKE_YES, Menu(action="revoke_yes")), (T.BTN_CANCEL, Menu(action="home"))]]))


@router.callback_query(Menu.filter(F.action == "revoke_yes"))
async def on_revoke_yes(cb: CallbackQuery, state: FSMContext, bot: Bot):
    await cb.answer()
    await state.clear()
    await cb.message.edit_reply_markup(reply_markup=None)
    await forget_user(bot, cb.from_user.id)
    await cb.message.answer(T.REVOKED)


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
async def on_home(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.clear()
    if ready(cb.from_user.id):
        await show_menu(cb.message, cb.from_user.id)
    else:
        await cb.message.answer(T.EXPIRED)


@router.callback_query()
async def on_stale(cb: CallbackQuery):
    await cb.answer(T.EXPIRED, show_alert=True)


@router.message()
async def on_other(message: Message, state: FSMContext):
    uid = message.from_user.id
    if ready(uid):
        await state.clear()
        await show_menu(message, uid)
    else:
        await message.answer("Send /start to begin.")


# --- entry point -----------------------------------------------------------------
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
    asyncio.create_task(nightly.loop(bot, db, GROUP_ID))
    log.info("starting polling as @%s", (await bot.me()).username)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
