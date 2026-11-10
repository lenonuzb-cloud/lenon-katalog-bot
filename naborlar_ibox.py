"""Bir martalik: naborlarni iBox bilan tenglashtirish.
  • iBox'da bor, botda yo'q naborlar (33/39/44) — qo'shiladi;
  • «LENON nabor 46» bo'limi ochiladi (17 ta nabor, 115$), guruhda mavzu ochishga harakat qilinadi;
  • rasmi YO'Q naborlarga iBox'dagi rasm qo'yiladi (logosiz). Rasmi bor (logoli) naborlarga tegilmaydi.
Ma'lumot: ibox_naborlar.json (iBox'dan olingan: kod, narx, qoldiq, rasm manzili)."""
import asyncio
import html
import json
import logging
import os

from aiohttp import ClientSession, ClientTimeout

import config
import publisher
from store import store
from tg import bot, TelegramError

log = logging.getLogger("naborlar_ibox")
HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE46 = {"pieces": "46", "price": "115$", "items": []}


def _n(s):
    return "".join(ch for ch in str(s or "").lower() if ch.isalnum())


def ensure_section46() -> bool:
    if store.section("nabor46"):
        return False
    secs = store.s["sections"]
    pos = next((i + 1 for i, s in enumerate(secs) if s["key"] == "nabor39"), len(secs))
    secs.insert(pos, {"key": "nabor46", "title": "LENON nabor 46", "kind": "nabor"})
    store.s.setdefault("nabor_templates", {})["nabor46"] = dict(TEMPLATE46)
    store.settings.setdefault("topics", {}).setdefault("nabor46", None)
    store.save()
    return True


async def _topic46(owner):
    """Guruhda «LENON nabor 46» mavzusini ochadi (bot huquqi bo'lsa)."""
    tp = store.settings.setdefault("topics", {})
    if tp.get("nabor46"):
        return True
    gid = store.settings.get("group_id")
    if not gid:
        return False
    try:
        r = await bot.call("createForumTopic", chat_id=gid, name="LENON nabor 46")
        tp["nabor46"] = r["message_thread_id"]
        store.save()
        return True
    except TelegramError as e:
        log.warning("mavzu ochilmadi: %s", e)
        return False


async def _tg_photo(s, owner, url):
    async with s.get(url) as r:
        if r.status != 200:
            raise RuntimeError(f"rasm olinmadi {r.status}")
        data = await r.read()
    ext = "png" if url.lower().endswith(".png") else "jpg"
    m = await bot.call("sendPhoto", files={"photo": (f"nabor.{ext}", data, f"image/{'png' if ext == 'png' else 'jpeg'}")},
                       chat_id=str(owner), disable_notification="true")
    await bot.safe_delete(owner, m["message_id"])
    return m["photo"][-1]["file_id"]


async def run(owner):
    data = json.load(open(os.path.join(HERE, "ibox_naborlar.json"), encoding="utf-8"))
    new46 = ensure_section46()
    added, photos, kept = {}, {}, 0
    touched = []
    async with ClientSession(timeout=ClientTimeout(total=60)) as s:
        for key, rows in data.items():
            if not store.section(key):
                continue
            have = {_n(d["title"]): d for d in store.designs.values()
                    if not d.get("deleted") and d["section"] == key}
            for r in rows:
                d = have.get(_n(r["code"]))
                if not d:
                    did = store.add_design(key, r["code"], log_event=False, mark_new=False)
                    d = store.designs[did]
                    d["in_stock"] = r["stock"] > 0
                    added.setdefault(key, []).append(r["code"])
                if d.get("photos"):
                    kept += 1
                elif r.get("photo"):
                    try:
                        d["photos"] = [await _tg_photo(s, owner, r["photo"])]
                        photos.setdefault(key, []).append(r["code"])
                        await asyncio.sleep(0.4)
                    except Exception as e:  # noqa
                        log.warning("nabor rasmi %s: %s", r["code"], e)
                touched.append(d["id"])
            store.save()
    topic_ok = await _topic46(owner)
    for i, did in enumerate(dict.fromkeys(touched)):
        publisher.queue_update(did, delay=10 + i * 4)
    for key in data:
        if store.section(key):
            publisher.queue_index(key, delay=30 + len(touched) * 4)
    lines = ["📦 <b>Naborlar iBox bilan tenglashtirildi</b>"]
    if new46:
        lines.append("🆕 «LENON nabor 46» bo'limi ochildi (17 ta nabor, 115$).")
    for k, v in added.items():
        lines.append(f"➕ {html.escape(store.section(k)['title'])}: {len(v)} ta yangi — {html.escape(', '.join(v))}")
    for k, v in photos.items():
        lines.append(f"🖼 {html.escape(store.section(k)['title'])}: {len(v)} ta naborga iBox rasmi qo'yildi")
    lines.append(f"✋ Rasmi bor {kept} ta naborga tegilmadi.")
    no46 = [d["title"] for d in store.designs.values() if d["section"] == "nabor46" and not d.get("photos")]
    if no46:
        lines.append(f"⚠️ nabor 46 da iBox'da ham rasm yo'q: {len(no46)} ta — rasmlarini botga yuboring.")
    if not topic_ok:
        lines.append("⚠️ Guruhda «LENON nabor 46» mavzusini ochib, ichida <code>/bolim nabor46</code> deb yozing.")
    lines.append("Guruhdagi postlar bir necha daqiqada yangilanadi.")
    await bot.send(owner, "\n".join(lines)[:4000])
