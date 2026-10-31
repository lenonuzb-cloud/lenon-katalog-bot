"""Avto-aylantirish (LENON_UZ guruhi uchun).

Har kuni 08:00–22:00 (Toshkent vaqti), har soat boshida navbatdagi tovar guruhga chiqadi:
rasm + jadval (nomi, kodi, soni, narxi) + agent tugmalari + «📒 Katalog» tugmasi.
Navbat ketma-ket: chiqqan tovar navbat oxiriga o'tadi. Yangi tovarlar navbat oxiriga qo'shiladi.
/aylantir — navbatdagisini hozir joylaydi, /navbat — holat."""
import logging
from datetime import datetime

import config
import publisher
from store import store
from tg import bot

log = logging.getLogger("rotation")


def _rot() -> dict:
    r = store.settings.setdefault("rot", {"queue": [], "last_slot": "", "posted": 0})
    r.setdefault("queue", [])
    return r


def sync_queue() -> list:
    """Navbatni katalog bilan moslaydi: o'chirilganlar chiqariladi, yangilari oxiriga qo'shiladi."""
    r = _rot()
    alive = {d["id"] for d in store.designs.values() if not d.get("deleted") and not d.get("pinned")}
    q = [x for x in r["queue"] if x in alive]
    for d in sorted(store.designs.values(), key=lambda d: (d.get("created", 0), d.get("order", 0))):
        if d["id"] in alive and d["id"] not in q:
            q.append(d["id"])
    r["queue"] = q
    return q


async def post_next(reason="jadval") -> str | None:
    """Navbatdagi tovarni joylaydi va navbat oxiriga o'tkazadi. Qaytaradi: tovar nomi."""
    q = sync_queue()
    if not q:
        return None
    did = q.pop(0)
    q.append(did)
    d = store.designs[did]
    ids = await publisher.send_copy(did)
    import dedup
    await dedup.after_post(did, ids)
    r = _rot()
    r["posted"] = r.get("posted", 0) + 1
    r["last_title"] = d["title"]
    r["last_at"] = datetime.now(config.TZ).strftime("%d.%m %H:%M")
    store.save()
    store.event("edit", f"Avto-post ({reason}): {d['title']}", did)
    return d["title"]


async def post_now(design_id) -> str:
    """Tanlangan tovarni hozir joylaydi va navbat oxiriga qo'yadi («✅ Guruhga joylash»)."""
    q = sync_queue()
    if design_id in q:
        q.remove(design_id)
    q.append(design_id)
    ids = await publisher.send_copy(design_id)
    import dedup
    await dedup.after_post(design_id, ids)
    r = _rot()
    r["posted"] = r.get("posted", 0) + 1
    r["last_title"] = store.designs[design_id]["title"]
    r["last_at"] = datetime.now(config.TZ).strftime("%d.%m %H:%M")
    store.save()
    return store.designs[design_id]["title"]


async def tick():
    """Fon tsiklidan har daqiqada chaqiriladi: kerakli soat bo'lsa — bitta post."""
    if not config.ROTATION or not store.settings.get("group_id"):
        return
    import dedup
    try:
        await dedup.sweep()
    except Exception as e:  # noqa
        log.warning("Eski postlarni o'chirishda xato: %s", e)
    now = datetime.now(config.TZ)
    if not (config.ROT_FIRST_HOUR <= now.hour <= config.ROT_LAST_HOUR) or now.minute > 20:
        return
    slot = now.strftime("%Y-%m-%d %H")
    r = _rot()
    if r.get("last_slot") == slot:
        return
    r["last_slot"] = slot
    store.save()
    try:
        await post_next("jadval")
    except Exception as e:  # noqa
        log.warning("Avto-post xatosi: %s", e)
        owner = store.settings.get("owner_id")
        if owner:
            try:
                await bot.send(owner, f"⚠️ Avto-post xatosi: {publisher.esc(e)}")
            except Exception:  # noqa
                pass


def status_text() -> str:
    q = sync_queue()
    r = _rot()
    per_day = config.ROT_LAST_HOUR - config.ROT_FIRST_HOUR + 1
    days = len(q) / per_day if per_day else 0
    nxt = "\n".join(f"{i + 1}. {publisher.esc(store.designs[x]['title'])}" for i, x in enumerate(q[:5]))
    last = (f"\nOxirgi post: <b>{publisher.esc(r.get('last_title', '—'))}</b> ({r.get('last_at', '')})"
            if r.get("last_title") else "")
    return (f"🔁 <b>Avto-aylantirish</b>\n"
            f"Har kuni {config.ROT_FIRST_HOUR:02d}:00–{config.ROT_LAST_HOUR:02d}:00, har soat boshida 1 ta tovar "
            f"(kuniga {per_day} ta).\n"
            f"Navbatda: <b>{len(q)}</b> ta tovar — taxminan {days:.0f} kunda bir aylanib chiqadi.\n"
            f"Jami chiqqan postlar: {r.get('posted', 0)}{last}\n\n<b>Keyingilari:</b>\n{nxt}\n\n"
            f"/aylantir — navbatdagisini hozir joylash")


# ---------------- qadalgan (doimiy) postlar ----------------
async def pin_design(design_id) -> str:
    """Tovarni aylantirishdan chiqaradi, guruhga joylaydi va tepaga qadaydi.
    Qolmadi/keldi o'zgarsa — shu post joyida yangilanadi."""
    d = store.designs[design_id]
    gid = store.settings.get("group_id")
    if d.get("pinned") and d.get("msg"):
        try:
            await bot.call("unpinChatMessage", chat_id=gid, message_id=d["msg"]["message_id"])
        except Exception:  # noqa
            pass
        try:
            await bot.call("deleteMessage", chat_id=gid, message_id=d["msg"]["message_id"])
        except Exception:  # noqa
            pass
    import dedup
    for c in dedup._st()["posts"].pop(design_id, []):  # aylantirishdagi eski nusxasi o'chadi
        await dedup._delete(c["ids"])
    d["msg"] = None
    await publisher._post_new(d, "design", gid, -1)
    d["pinned"] = True
    sync_queue()
    store.save()
    await bot.call("pinChatMessage", chat_id=gid, message_id=d["msg"]["message_id"], disable_notification=True)
    return d["title"]


async def unpin_design(design_id) -> str:
    """Qadalgan tovarni yana aylantirishga qaytaradi (post guruhda qoladi, faqat qadash olinadi)."""
    d = store.designs[design_id]
    gid = store.settings.get("group_id")
    if d.get("msg"):
        try:
            await bot.call("unpinChatMessage", chat_id=gid, message_id=d["msg"]["message_id"])
        except Exception:  # noqa
            pass
    d["pinned"] = False
    d["msg"] = None
    sync_queue()
    store.save()
    return d["title"]


PIN_ONCE = ["Oq farfor", "Farfor zarli", "Oq torix"]


async def pin_once():
    """Bir martalik: egasi so'ragan 3 ta tovarni qadaydi (tartib: oxirgisi eng tepada ko'rinadi)."""
    import asyncio
    if store.settings.get("pin_v1") or not store.settings.get("group_id"):
        return
    await asyncio.sleep(20)
    done = []
    for title in reversed(PIN_ONCE):
        d = store.find_design(title)
        if d and not d.get("deleted"):
            try:
                done.append(await pin_design(d["id"]))
            except Exception as e:  # noqa
                await bot.send(store.settings.get("owner_id"), f"⚠️ «{publisher.esc(title)}» ni qadab bo'lmadi: "
                                                                f"{publisher.esc(e)}")
            await asyncio.sleep(3)
    store.settings["pin_v1"] = True
    store.save()
    owner = store.settings.get("owner_id")
    if owner and done:
        await bot.send(owner, "📌 Guruhga joylab, tepaga qadaldi (aylantirishdan chiqarildi):\n"
                       + "\n".join("• " + publisher.esc(t) for t in done)
                       + "\n\nQolmadi/keldi belgilasangiz — qadalgan post o'zi yangilanadi.\n"
                         "Boshqa tovarni qadash: /qada nomi · aylantirishga qaytarish: /qadama nomi")
