"""Mahsulot rasmlarini qaytadan tayyorlash (masalan, yozuvdan narx olib tashlanganda).

Har bir mahsulot rasmining asl nusxasi (photo_raw) saqlanadi. Shundan rasm qaytadan
logo + yangi yozuv bilan tayyorlanadi. Asl nusxasi saqlanmagan, lekin bot orqali
yozuv bilan qo'yilgan rasmlar uchun asl rasm egasining chatidan topib olinadi."""
import asyncio
import logging
import re

import images
import ops
import publisher
from store import store
from tg import bot, TelegramError

log = logging.getLogger("relabel")

import time

DAY = 24 * 3600          # oxirgi 24 soat ichida qo'yilgan rasmlar qayta ko'rib chiqiladi
_RESULT = re.compile(r"^✅ (.+) \((.+)\) — rasm qo'yildi$")


def _items():
    for d in store.designs.values():
        if d.get("deleted"):
            continue
        for c in d.get("categories", []):
            for it in c["items"]:
                yield d, it


def _largest(m):
    if m.get("photo"):
        return m["photo"][-1]
    doc = m.get("document")
    if doc and str(doc.get("mime_type", "")).startswith("image/"):
        return doc
    return None


async def _unique_id(fid):
    try:
        f = await bot.call("getFile", file_id=fid)
        return f.get("file_unique_id")
    except TelegramError:
        return None


async def recover_raw(uid, top_id) -> int:
    """Egasining chatidagi xabarlardan yozuvli rasmlarning asl nusxasini topadi."""
    need = [(d, it) for d, it in _items()
            if it.get("photo") and not it.get("photo_raw") and it.get("updated", 0) >= time.time() - DAY]
    if not need:
        return 0
    me = bot.me.get("id")
    msgs, copies, old_streak = [], [], 0
    for mid in range(top_id - 1, 0, -1):
        try:
            m = await bot.call("forwardMessage", chat_id=uid, from_chat_id=uid, message_id=mid,
                               disable_notification=True, _retries=6)
        except TelegramError:
            continue
        copies.append(m["message_id"])
        if len(copies) >= 90:
            await _del(uid, copies)
            copies = []
        date = (m.get("forward_origin") or {}).get("date") or m.get("forward_date") or 0
        if date and date < time.time() - DAY:
            old_streak += 1
            if old_streak >= 5:
                break
            continue
        old_streak = 0
        sender = ((m.get("forward_origin") or {}).get("sender_user") or {}).get("id")
        msgs.append((mid, sender, m))
    await _del(uid, copies)
    msgs.sort(key=lambda x: x[0])

    # partiyalar: ketma-ket kelgan foydalanuvchi rasmlari + undan keyingi bot natijalari
    batches, cur = [], None
    for mid, sender, m in msgs:
        ph = _largest(m)
        if sender == uid and ph:
            if cur is None or cur["results"]:
                cur = {"raws": [], "results": []}
                batches.append(cur)
            cur["raws"].append(ph["file_id"])
        elif sender == me and m.get("photo") and cur is not None:
            mt = _RESULT.match((m.get("caption") or "").strip())
            if mt:
                cur["results"].append((mt.group(1), mt.group(2), m["photo"][-1]["file_unique_id"]))
    found = 0
    for b in batches:
        if not b["results"] or len(b["raws"]) != len(b["results"]):
            continue  # o'tkazib yuborilgan rasm bo'lsa — juftlashtirish ishonchsiz
        for raw, (name, title, uniq) in zip(b["raws"], b["results"]):
            for d, it in need:
                if d["title"] == title and it["name"] == name and not it.get("photo_raw"):
                    if await _unique_id(it["photo"]) == uniq:
                        it["photo_raw"] = raw
                        found += 1
                    break
    if found:
        store.save()
    return found


async def _del(uid, ids):
    if not ids:
        return
    try:
        await bot.call("deleteMessages", chat_id=uid, message_ids=ids)
    except TelegramError:
        for x in ids:
            await bot.safe_delete(uid, x)


async def run(uid):
    note = await bot.send(uid, "🖼 Mahsulot rasmlari qaytadan tayyorlanmoqda (yangi yozuv bilan)…\n"
                               "Bu chatda xabarlar paydo bo'lib, o'chib turishi mumkin — bu normal.")
    try:
        recovered = await recover_raw(uid, note["message_id"])
    except Exception as e:  # noqa
        log.warning("Asl rasmlarni topishda xato: %s", e)
        recovered = 0
    done, touched, failed = 0, set(), []
    for d, it in list(_items()):
        raw = it.get("photo_raw")
        if not raw:
            continue
        try:
            data = await bot.download(raw)
            out = await asyncio.get_event_loop().run_in_executor(None, images.process, data, True,
                                                                 ops.item_label(d, it))
            m = await bot.call("sendPhoto", files={"photo": ("rasm.jpg", out, "image/jpeg")},
                               chat_id=str(uid), disable_notification="true")
            it["photo"] = m["photo"][-1]["file_id"]
            await bot.safe_delete(uid, m["message_id"])
            done += 1
            touched.add(d["id"])
        except Exception as e:  # noqa
            failed.append(f"{it['name']} ({d['title']})")
            log.warning("Qayta tayyorlanmadi %s: %s", it["name"], e)
        await asyncio.sleep(0.3)
    store.save()
    for did in touched:
        publisher.queue_update(did, delay=5)
    missing = [f"{it['name']} ({d['title']})" for d, it in _items()
               if it.get("photo") and not it.get("photo_raw") and it.get("updated", 0) >= time.time() - DAY]
    text = f"✅ {done} ta mahsulot rasmi yangi yozuv bilan qayta tayyorlandi."
    if recovered:
        text += f"\n(shulardan {recovered} tasining asl rasmi chatdan topildi)"
    if failed:
        text += "\n⚠️ Bo'lmadi: " + ", ".join(failed[:20])
    if missing:
        text += ("\n\nℹ️ Bu rasmlarning asl nusxasi topilmadi, ularni qaytadan yuklang:\n• "
                 + "\n• ".join(missing[:30]))
    await bot.send(uid, text)
