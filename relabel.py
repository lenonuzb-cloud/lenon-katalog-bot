"""Katalogdagi barcha mahsulot rasmlarini yangi yozuv (o'lcham / nom / kod, narxsiz) bilan moslash.

Har bir rasm tekshiriladi:
  • yozuvi yo'q        → yozuv qo'shiladi
  • eski (narxli) yozuv → eski yozuv o'chirilib, yangisi yoziladi (asl nusxa bo'lsa — undan qaytadan)
  • yozuvi to'g'ri      → tegilmaydi
Bajarilganlari belgilanadi (label_v), shuning uchun to'xtab qolsa ham davomidan ishlaydi."""
import asyncio
import logging

import images
import ops
import publisher
from store import store
from tg import bot, TelegramError

log = logging.getLogger("relabel")
LABEL_V = 2
_running = False


def _items():
    for d in store.designs.values():
        if d.get("deleted"):
            continue
        for c in d.get("categories", []):
            for it in c["items"]:
                yield d, it


async def run(uid, force=False):
    global _running
    if _running:
        if uid:
            await bot.send(uid, "⏳ Rasmlar allaqachon tayyorlanmoqda, biroz kuting.")
        return
    _running = True
    try:
        await _run(uid, force)
    finally:
        _running = False


async def _run(uid, force):
    todo = [(d, it) for d, it in _items() if it.get("photo") and (force or it.get("label_v") != LABEL_V)]
    note = None
    if uid:
        note = await bot.send(uid, f"🖼 {len(todo)} ta mahsulot rasmi tekshirilmoqda va yangi yozuv bilan "
                                   f"moslanmoqda (o'lcham / nom / kod)…", disable_notification=True)
    stats = {"ok": 0, "added": 0, "fixed": 0, "err": 0}
    touched = set()
    for n, (d, it) in enumerate(todo, 1):
        new_lines = ops.item_label(d, it)
        old_lines = new_lines + [it.get("price", "")]
        try:
            if it.get("photo_raw"):
                raw = await bot.download(it["photo_raw"])
                out = await asyncio.get_event_loop().run_in_executor(None, images.process, raw, True, new_lines)
                state = "fixed"
            else:
                data = await bot.download(it["photo"])
                out, state = await asyncio.get_event_loop().run_in_executor(
                    None, images.relabel_image, data, old_lines, new_lines)
            if out is not None and uid:
                m = await bot.call("sendPhoto", files={"photo": ("rasm.jpg", out, "image/jpeg")},
                                   chat_id=str(uid), disable_notification="true")
                it["photo"] = m["photo"][-1]["file_id"]
                await bot.safe_delete(uid, m["message_id"])
                touched.add(d["id"])
            it["label_v"] = LABEL_V
            stats[state] += 1
        except Exception as e:  # noqa
            stats["err"] += 1
            log.warning("Rasm moslanmadi %s (%s): %s", it["name"], d["title"], e)
        if n % 10 == 0:
            store.save(backup=False)
            if note:
                try:
                    await bot.call("editMessageText", chat_id=uid, message_id=note["message_id"],
                                   text=f"🖼 Rasmlar moslanmoqda: {n}/{len(todo)}…")
                except TelegramError:
                    pass
        await asyncio.sleep(0.2)
    store.save()
    for i, did in enumerate(touched):
        publisher.queue_update(did, delay=5 + i * 3)
    if uid:
        text = (f"✅ Rasmlar tayyor ({len(todo)} ta tekshirildi):\n"
                f"• Yozuv qo'shildi: {stats['added']}\n"
                f"• Narx olib tashlandi / yozuv yangilandi: {stats['fixed']}\n"
                f"• O'zi to'g'ri edi: {stats['ok']}")
        if stats["err"]:
            text += f"\n⚠️ Xato: {stats['err']} ta (keyingi safar qayta urinadi)"
        if note:
            await bot.safe_delete(uid, note["message_id"])
        await bot.send(uid, text)
    store.settings["relabel_done"] = LABEL_V if not stats["err"] else 0
    store.save()
