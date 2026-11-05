"""Yangi qo'yilgan rasmlar guruhning «Rasmlar» bo'limiga ham tushadi.

Rasm qo'yilganda navbatga qo'shiladi; 8 soniya yangi rasm kelmasa — 10 tadan albom qilib yuboriladi
(ko'p rasm birdan qo'yilsa ham guruh to'lib ketmaydi). Faqat LENON 55 (bo'limli guruh)."""
import asyncio
import logging

import config
from store import store
from tg import bot

log = logging.getLogger("rasmlarga")
_queue: list = []          # (file_id, caption)
_task = None


def add(file_id, caption):
    global _task
    if config.ROTATION or not file_id:
        return
    st = store.settings
    if not st.get("group_id") or not st.get("topics", {}).get("rasmlar"):
        return
    _queue.append((file_id, caption[:1000]))
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return
    if _task is None or _task.done():
        _task = loop.create_task(_flush())


async def _flush():
    last = -1
    while len(_queue) != last:          # yangi rasm kelishini kutamiz
        last = len(_queue)
        await asyncio.sleep(8)
    gid = store.settings.get("group_id")
    thread = store.settings.get("topics", {}).get("rasmlar")
    while _queue:
        chunk, _queue[:] = _queue[:10], _queue[10:]
        try:
            if len(chunk) == 1:
                await bot.call("sendPhoto", chat_id=gid, message_thread_id=thread, photo=chunk[0][0],
                               caption=chunk[0][1], disable_notification=True)
            else:
                await bot.call("sendMediaGroup", chat_id=gid, message_thread_id=thread, disable_notification=True,
                               media=[{"type": "photo", "media": f, "caption": c} for f, c in chunk])
        except Exception as e:  # noqa
            log.warning("Rasmlar bo'limiga yuborilmadi: %s", e)
        await asyncio.sleep(3)
