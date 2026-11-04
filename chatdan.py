"""Egasining bot bilan chatidagi eski rasmlardan katalog rasmlarini qaytarish.

Bot ilgari egasiga yuborgan rasmlar chatda turibdi (ular himoyalanmagan):
  • «✅ <nom> (<dizayn>) — rasm qo'yildi»  — mahsulot rasmi qo'yilganda;
  • «<dizayn nomi>»                          — dizayn rasmi (🖼 Rasmlar eksporti);
  • «<nom> · <kod> · <narx>»                 — mahsulot rasmi (🖼 Rasmlar eksporti, dizayndan keyin keladi).
Bot chatdagi har bir xabarni o'ziga jimgina uzatib (darhol o'chiriladi), shu yozuvlar bo'yicha
rasmni to'g'ri mahsulotga qaytaradi. Eng oxirgi qo'yilgan rasm olinadi."""
import asyncio
import html
import logging
import re
import time

from store import store
from tg import bot, TelegramError

log = logging.getLogger("chatdan")
_running = False


def _n(s):
    return re.sub(r"[^0-9a-z]+", "", (s or "").lower())


def _designs():
    return [d for d in store.designs.values() if not d.get("deleted")]


def _design_by_title(t):
    t = _n(t)
    return next((d for d in _designs() if _n(d["title"]) == t), None)


def _item(d, name, code=""):
    best = None
    for c in d["categories"]:
        for it in c["items"]:
            if _n(it["name"]) == _n(name):
                if code and _n(it.get("code")) == _n(code):
                    return it
                best = best or it
    return best


async def run(owner, progress=True):
    global _running
    if _running:
        return
    _running = True
    try:
        return await _run(owner, progress)
    finally:
        _running = False


async def _run(owner, progress):
    me = bot.me.get("id")
    probe = await bot.send(owner, "🔎 Chatdagi eski rasmlar qidirilmoqda… (xabarlar bir lahza paydo bo'lib, o'chib turadi)",
                           disable_notification=True)
    top = probe["message_id"]
    item_ph, cover_ph = {}, {}       # item_id -> (mid, file_id) ; design_id -> [(mid, file_id)]
    ctx = None                        # eksportdagi joriy dizayn
    seen = 0
    t0 = time.time()
    copies = []
    for mid in range(1, top):
        try:
            m = await bot.call("forwardMessage", chat_id=owner, from_chat_id=owner, message_id=mid,
                               disable_notification=True, _retries=8)
        except TelegramError:
            continue
        except Exception:  # noqa
            continue
        copies.append(m["message_id"])
        if len(copies) >= 50:
            await _cleanup(owner, copies)
            copies = []
        seen += 1
        if progress and seen % 150 == 0:
            try:
                await bot.call("editMessageText", chat_id=owner, message_id=top,
                               text=f"🔎 Chatdagi eski rasmlar qidirilmoqda… {mid}/{top} "
                                    f"(topildi: {len(item_ph)} ta mahsulot rasmi)")
            except Exception:  # noqa
                pass
        origin = (m.get("forward_origin") or {}).get("sender_user") or {}
        if origin.get("id") != me:
            continue
        cap = (m.get("caption") or "").strip()
        if not m.get("photo"):
            continue
        fid = m["photo"][-1]["file_id"]
        g = re.match(r"^✅\s*(.+?)\s*\((.+)\)\s*—\s*rasm qo'yildi", cap)
        if g:
            d = _design_by_title(g.group(2))
            it = d and _item(d, g.group(1))
            if it:
                item_ph[it["id"]] = (mid, fid)
            continue
        d = _design_by_title(cap)
        if d:
            ctx = (d, mid)
            cover_ph.setdefault(d["id"], []).append((mid, fid))
            continue
        parts = [p.strip() for p in cap.split("·")]
        if len(parts) >= 2:
            name, code = parts[0], parts[1] if len(parts) >= 3 else ""
            it = None
            if ctx and mid - ctx[1] < 120:
                it = _item(ctx[0], name, code)
            if not it:   # nom+kod faqat bitta dizaynda bo'lsa
                cands = [x for x in (_item(dd, name, code) for dd in _designs()) if x]
                it = cands[0] if len(cands) == 1 else None
            if it:
                item_ph[it["id"]] = (mid, fid)
        await asyncio.sleep(0.03)
    await _cleanup(owner, copies)
    # qo'llash: faqat rasmi yo'qlarga
    added, covers, by_design = 0, 0, {}
    for d in _designs():
        for c in d["categories"]:
            for it in c["items"]:
                if it["id"] in item_ph and not it.get("photo"):
                    it["photo"] = item_ph[it["id"]][1]
                    it["label_v"] = 2
                    added += 1
                    by_design[d["title"]] = by_design.get(d["title"], 0) + 1
        if d["id"] in cover_ph and not d.get("photos"):
            fids = []
            for _, f in sorted(cover_ph[d["id"]]):
                if f not in fids:
                    fids.append(f)
            d["photos"] = fids[-20:]
            covers += 1
    store.save()
    await bot.safe_delete(owner, top)
    lines = [f"✅ <b>Chatdagi rasmlardan tiklandi</b> ({int(time.time() - t0) // 60} daqiqa):",
             f"• {added} ta mahsulot rasmi", f"• {covers} ta dizayn rasmi"]
    if by_design:
        lines.append("")
        lines += [f"{html.escape(k)}: {v}" for k, v in sorted(by_design.items())]
    no = sum(1 for d in _designs() for c in d["categories"] for it in c["items"] if not it.get("photo"))
    lines.append(f"\nHali rasmi yo'q mahsulotlar: {no} ta")
    await bot.send(owner, "\n".join(lines)[:4000])
    return added, covers


async def _cleanup(owner, ids):
    if not ids:
        return
    try:
        await bot.call("deleteMessages", chat_id=owner, message_ids=ids)
    except Exception:  # noqa
        for i in ids:
            try:
                await bot.call("deleteMessage", chat_id=owner, message_id=i)
            except Exception:  # noqa
                pass
