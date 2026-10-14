"""7-agent: katalogdagi o'zgarishlar haqida guruh a'zolarini (Muhokama bo'limida) xabardor qiladi.
Bir necha o'zgarish bo'lsa, ularni bitta umumiy xabarga yig'adi."""
import asyncio
import html
import logging

import config
from store import store
from tg import bot

log = logging.getLogger("notify")

_buf = {"qolmadi": [], "keldi": [], "new": [], "edit": []}
_task = None
error_hook = None


def add(kind: str, text: str):
    global _task
    if kind not in _buf:
        kind = "edit"
    if text not in _buf[kind]:
        _buf[kind].append(text)
    if _task is None or _task.done():
        _task = asyncio.get_event_loop().create_task(_flush_later())


async def _flush_later():
    await asyncio.sleep(config.NOTIFY_DELAY)
    await flush()


def _group(lst):
    """'Salat lord (LENON 01-AL)', 'Salat lord (LENON 02-WL)' ... -> 'Salat lord — 14 ta dizaynda'."""
    import re
    groups, order = {}, []
    for x in lst:
        m = re.match(r"^(.*?) \((.+)\)$", x)
        key = m.group(1) if m else x
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(m.group(2) if m else None)
    out = []
    for k in order:
        ds = [d for d in groups[k] if d]
        if len(ds) > 2:
            out.append(f"{k} — {len(ds)} ta dizaynda")
        elif ds:
            out.append(f"{k} ({', '.join(ds)})")
        else:
            out.append(k)
    return out


def _fmt(lst, limit=25):
    lst = _group(lst)
    out = [f"• {html.escape(x, quote=False)}" for x in lst[:limit]]
    if len(lst) > limit:
        out.append(f"… va yana {len(lst) - limit} ta")
    return "\n".join(out)


async def flush():
    if not any(_buf.values()):
        return
    parts = ["📢 <b>Katalog yangilandi!</b>"]
    if _buf["qolmadi"]:
        parts.append("❌ <b>Qolmadi:</b>\n" + _fmt(_buf["qolmadi"]))
    if _buf["keldi"]:
        parts.append("✅ <b>Yana keldi:</b>\n" + _fmt(_buf["keldi"]))
    if _buf["new"]:
        parts.append("🆕 <b>Yangi:</b>\n" + _fmt(_buf["new"]))
    if _buf["edit"]:
        parts.append("✏️ <b>O'zgartirildi:</b>\n" + _fmt(_buf["edit"]))
    parts.append("Jadval yoki katalog orqali tekshirib oling 👇")
    text = "\n\n".join(parts)
    for v in _buf.values():
        v.clear()
    st = store.settings
    gid, thread = st.get("group_id"), st["topics"].get("muhokama")
    if not gid or not thread:
        return
    markup = {"inline_keyboard": [[{"text": "📒 Katalogni ochish", "url": f"https://t.me/{bot.username}?startapp"}]]}
    try:
        await bot.send(gid, text[:4000], message_thread_id=thread, reply_markup=markup)
        store.event("notify", "Guruhga xabar yuborildi")
    except Exception as e:  # noqa
        if error_hook:
            await error_hook("Muhokama'ga xabar yuborilmadi", e)
