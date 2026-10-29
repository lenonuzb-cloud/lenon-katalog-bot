"""LENON_UZ: guruhda bitta tovarning faqat bitta (eng yangi) posti qolishi uchun.

1) Har safar tovar qayta joylanganda — o'sha tovarning eski posti o'chiriladi.
2) /tozala (va yangi o'rnatilganda bir marta avtomatik) — guruhdagi botning eski postlarini
   ko'rib chiqadi: har bir tovardan eng yangisi qoladi, qolgan dublikatlar o'chiriladi.
3) Agar Telegram 48 soatdan eski postni o'chirishga ruxsat bermasa — har bir post 46 soatdan
   keyin o'chiriladi (shunda dublikat hech qachon to'planmaydi).

Faqat botning O'Z postlari o'chiriladi; a'zolarning xabarlari va botning boshqa xabarlari
(katalog havolasi, e'lonlar) tegilmaydi."""
import asyncio
import json
import logging
import os
import re
import time

import config
from store import store
from tg import bot, TelegramError

log = logging.getLogger("dedup")
H = 3600
OLD = 48 * H          # Telegram cheklovi
SAFE = 46 * H         # cheklovdan oldin o'chirish (zaxira rejim)
_scan_task = None
_DELAY = float(os.getenv("DEDUP_DELAY", "1.1"))  # Telegram: bitta chatga ~1 xabar/soniya


def _st() -> dict:
    s = store.settings.setdefault("dedup", {})
    s.setdefault("posts", {})     # design_id -> [{"ids": [...], "t": unix}]
    s.setdefault("old_ok", None)  # True: 48 soatdan eskisini ham o'chira oladi
    return s


def _link(mid) -> str:
    gid = str(store.settings.get("group_id", ""))
    return f"https://t.me/c/{gid.replace('-100', '', 1)}/{mid}"


async def _delete(ids) -> bool:
    gid = store.settings.get("group_id")
    ok = True
    for mid in ids:
        try:
            await bot.call("deleteMessage", chat_id=gid, message_id=mid)
        except TelegramError as e:
            if "not found" in e.description.lower():
                continue  # allaqachon o'chirilgan
            ok = False
    return ok


def _learn(age, ok):
    """48 soatdan eski postni o'chira oldikmi — shuni eslab qoladi."""
    if age < OLD:
        return
    s = _st()
    if ok:
        s["old_ok"] = True
    elif s.get("old_ok") is not True:
        s["old_ok"] = False


async def after_post(design_id, ids):
    """Yangi post chiqqach — shu tovarning oldingi postlarini o'chiradi."""
    if not config.ROTATION or not ids:
        return
    s = _st()
    now = time.time()
    failed = []
    for c in s["posts"].get(design_id, []):
        age = now - c.get("t", now)
        ok = await _delete(c["ids"])
        _learn(age, ok)
        if not ok:
            failed += c["ids"][:1]
    s["posts"][design_id] = [{"ids": list(ids), "t": now}]
    store.save()
    if failed:
        await _tell(f"⚠️ «{store.designs[design_id]['title']}» ning eski posti o'chirilmadi "
                    f"(Telegram ruxsat bermadi). Qo'lda o'chiring:\n" + "\n".join(_link(m) for m in failed))


async def sweep():
    """Zaxira rejim: eski postlarni o'chirib bo'lmasa — har bir post 46 soatdan keyin o'chadi."""
    s = _st()
    if s.get("old_ok") is True:
        return
    now = time.time()
    changed = False
    for did, copies in list(s["posts"].items()):
        keep = []
        for c in copies:
            age = now - c.get("t", now)
            if SAFE <= age < OLD:
                await _delete(c["ids"])
                changed = True
            elif age < OLD:
                keep.append(c)
            else:
                changed = True
        s["posts"][did] = keep
    if changed:
        store.save()


async def _tell(text):
    owner = store.settings.get("owner_id")
    if owner:
        try:
            await bot.send(owner, text, disable_notification=True)
        except Exception:  # noqa
            pass


# ---------------- eski dublikatlarni tozalash ----------------
def _title_finder():
    items = []
    for d in store.designs.values():
        if d.get("deleted") or not d.get("title"):
            continue
        items.append((d["title"], d["id"]))
    items.sort(key=lambda x: -len(x[0]))
    pats = [(re.compile(r"(?<![\w\-])" + re.escape(t) + r"(?![\w\-])"), did) for t, did in items]

    def find(text):
        for p, did in pats:
            if p.search(text):
                return did
        return None
    return find


async def _forward_probe(owner, gid, mid):
    """Xabarni egasining chatiga jim forward qiladi va darhol o'chiradi. Qaytaradi: (xabar|None, xato)."""
    try:
        m = await bot.call("forwardMessage", chat_id=owner, from_chat_id=gid, message_id=mid,
                           disable_notification=True)
    except TelegramError as e:
        return None, e.description.lower()
    try:
        await bot.call("deleteMessage", chat_id=owner, message_id=m["message_id"])
    except TelegramError:
        pass
    return m, ""


async def clean(start=1, max_gap=1000, report=True):
    """Guruhdagi botning postlarini ko'rib chiqadi va har tovardan faqat eng yangisini qoldiradi."""
    gid = store.settings.get("group_id")
    owner = store.settings.get("owner_id")
    if not gid or not owner:
        return "Guruh yoki egasi sozlanmagan."
    if report:
        await _tell("🧹 Guruhdagi dublikat postlar tekshirilmoqda… Bu 20–40 daqiqa olishi mumkin. "
                    "Shu chatda bir zum paydo bo'lib yo'qolayotgan xabarlar — tekshiruv, e'tibor bermang.")
    find = _title_finder()
    me = bot.me.get("id")
    pinned = set()
    try:
        ch = await bot.call("getChat", chat_id=gid)
        if ch.get("pinned_message"):
            pinned.add(ch["pinned_message"]["message_id"])
    except TelegramError:
        pass
    found = {}      # design_id -> [(mid, date)]
    groups = {}     # media_group_id -> [mid]
    gtitle = {}     # media_group_id -> design_id
    # qayergacha ko'ramiz: bot kuzatayotgan eng yangi post (bo'lmasa — 1000 ta bo'sh joygacha)
    tracked_ids = [i for cs in _st()["posts"].values() for c in cs for i in c["ids"]]
    end = max(tracked_ids) if tracked_ids else None
    mid, gap = start, 0
    while (mid <= end) if end else (gap < max_gap):
        m, err = await _forward_probe(owner, gid, mid)
        await asyncio.sleep(_DELAY)
        if m is None:
            if "protected" in err or "can't be forwarded" in err:
                return "Guruhda «kontentni himoyalash» yoqilgan — tekshirib bo'lmadi."
            gap += 1
            mid += 1
            continue
        gap = 0
        org = m.get("forward_origin") or {}
        sender = (org.get("sender_user") or {}).get("id") or (m.get("forward_from") or {}).get("id")
        if sender == me and mid not in pinned:
            date = org.get("date") or m.get("forward_date") or 0
            body = json.dumps({k: v for k, v in m.items() if k not in ("chat", "from", "forward_origin")},
                              ensure_ascii=False)
            did = find(body)
            # faqat tovar posti: rasm yoki jadvalli (boy) xabar — oddiy matnli xabarlar tegilmaydi
            post_like = bool(did) and ("photo" in m or any("rich" in k for k in m)
                                       or ("Narxi" in body and "Nomi" in body))
            mg = m.get("media_group_id")
            if mg:
                groups.setdefault(mg, []).append((mid, date))
                if post_like:
                    gtitle[mg] = did
            elif post_like:
                found.setdefault(did, []).append(([mid], date))
        mid += 1
    for mg, members in groups.items():
        if mg in gtitle:
            found.setdefault(gtitle[mg], []).append(([x for x, _ in members], members[0][1]))
    s = _st()
    now = time.time()
    deleted = 0
    failed = []
    for did, copies in found.items():
        copies.sort(key=lambda c: max(c[0]))
        newest = copies[-1]
        for ids, date in copies[:-1]:
            age = now - (date or now)
            ok = await _delete(ids)
            _learn(age, ok)
            if ok:
                deleted += 1
            else:
                failed.append(ids[0])
            await asyncio.sleep(0.4)
        tracked = s["posts"].get(did) or []
        if not any(newest[0][0] in c["ids"] for c in tracked):
            s["posts"][did] = [{"ids": newest[0], "t": newest[1] or now}]
    s["scan_done"] = True
    store.save()
    msg = f"🧹 Tozalash tugadi.\nTovarlar: {len(found)} ta, o'chirilgan dublikatlar: {deleted} ta."
    if not failed:
        msg += "\nHar bir tovardan faqat eng yangi posti qoldi."
    if failed:
        msg += (f"\n\n⚠️ {len(failed)} ta eski postni Telegram o'chirishga ruxsat bermadi "
                "(botda «Xabarlarni o'chirish» huquqi yo'q yoki post juda eski). Ularni qo'lda o'chiring:\n"
                + "\n".join(_link(x) for x in failed[:60]))
    if s.get("old_ok") is False:
        msg += ("\n\nℹ️ Bundan keyin dublikat to'planmasligi uchun har bir post 46 soatdan keyin "
                "o'zi o'chadi (Telegram eskiroq postni o'chirishga ruxsat bermaydi).")
    elif s.get("old_ok"):
        msg += "\n\n✅ Bundan keyin tovar qayta chiqqanda uning eski posti o'zi o'chadi."
    if report:
        await _tell(msg)
    return msg


def start_clean(report=True):
    global _scan_task
    if _scan_task and not _scan_task.done():
        return False
    _scan_task = asyncio.get_event_loop().create_task(_run_clean(report))
    return True


async def _run_clean(report):
    try:
        await clean(report=report)
    except Exception as e:  # noqa
        log.exception("tozalash")
        await _tell(f"⚠️ Tozalashda xato: {e}")


async def auto_clean_once():
    """Yangi o'rnatilgandan keyin bir marta: eski dublikatlarni tozalaydi."""
    # birinchi avto-post chiqishini kutamiz — shunda guruhdagi eng oxirgi xabar raqami ma'lum bo'ladi
    for _ in range(36 * 60):
        if not config.ROTATION or _st().get("scan_done"):
            return
        if _st()["posts"]:
            start_clean()
            return
        await asyncio.sleep(60)
