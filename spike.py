"""One-off check of the new Bot API features against the TEST bot and TEST group.

    .venv\\Scripts\\python spike.py <your_id> [<member_id>]

1. sends <your_id> the real rating screen (Serving, levels 1-5), then edits it to levels 2-6
2. if <member_id> is given (a regular member, NOT the owner/admin): sets their tag to "~~2.5",
   waits 20 s so you can look at the group, then removes it
Delete this file once everything works.
"""
import asyncio
import sys

from aiogram import Bot

from bot import BOT_TOKEN, GROUP_ID, discipline_screen


async def main(uid: int, member: int | None):
    async with Bot(BOT_TOKEN) as bot:
        data = {"scores": [], "start": 1, "header": ["Rating Anna", "Games together: 2 · last: 3 days ago"],
                "name": "Anna"}
        rich, kb = discipline_screen(data)
        msg = await bot.send_rich_message(chat_id=uid, rich_message=rich, reply_markup=kb)
        print("rating screen sent (levels 1-5)")
        await asyncio.sleep(3)
        rich, kb = discipline_screen({**data, "start": 2})
        await bot.edit_message_text(chat_id=uid, message_id=msg.message_id, rich_message=rich, reply_markup=kb)
        print("edited to levels 2-6 (buttons are inert here, the real bot handles them)")

        if member:
            m = await bot.get_chat_member(GROUP_ID, member)
            print(f"member status: {m.status}")
            print("set tag:", await bot.set_chat_member_tag(chat_id=GROUP_ID, user_id=member, tag="~~2.5"))
            print("check the group now; removing in 20 s...")
            await asyncio.sleep(20)
            print("remove tag:", await bot.set_chat_member_tag(chat_id=GROUP_ID, user_id=member, tag=""))


asyncio.run(main(int(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else None))
