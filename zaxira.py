"""Katalog zaxirasini (💾 lenon_baza.json) qo'lda tiklash.

Server (Render bepul) qayta ishga tushganda diski tozalanadi va bot ma'lumotni egasining chatidagi
mahkamlangan zaxira faylidan oladi. Agar u topilmasa yoki o'qib bo'lmasa — egasi istalgan eski
zaxira faylini botga qayta yuborib, katalogni tiklay oladi."""
import json
import logging

from store import store
from tg import bot

log = logging.getLogger("zaxira")
_pending: dict = {}   # uid -> file_id
hook = None           # main.py dagi backup_to_telegram (tiklash muvaffaqiyatsiz bo'lsa vaqtincha o'chiriladi)


def _summary(st) -> str:
    ds = [d for d in st.get("designs", {}).values() if not d.get("deleted")]
    n = sum(len(c.get("items", [])) for d in ds for c in d.get("categories", []))
    return f"{len(ds)} ta dizayn/nabor, {n} ta mahsulot"


def is_backup_doc(m) -> bool:
    doc = m.get("document") or {}
    return str(doc.get("file_name", "")).lower().endswith(".json")


async def offer(uid, m):
    if not store.is_owner(uid):
        await bot.send(uid, "Zaxiradan tiklashni faqat bot egasi qila oladi.")
        return
    doc = m["document"]
    try:
        st = json.loads((await bot.download(doc["file_id"])).decode("utf-8"))
        assert "designs" in st
    except Exception:  # noqa
        await bot.send(uid, "❗ Bu fayl katalog zaxirasi emas (💾 lenon_baza.json yoki baza.json bo'lishi kerak).")
        return
    _pending[uid] = doc["file_id"]
    try:
        from datetime import datetime
        import config
        saved = datetime.fromtimestamp(float(st.get("saved_at")), config.TZ).strftime("%d.%m.%Y %H:%M")
    except Exception:  # noqa
        saved = ""
    await bot.send(uid,
                   f"💾 Zaxira fayli: {_summary(st)}" + (f"\nSaqlangan vaqti: {saved}" if saved else "") +
                   f"\n\nHozirgi katalog: {_summary(store.s)}\n\n"
                   "Katalogni shu fayldagi holatga qaytaraymi? Hozirgi ma'lumot shu fayl bilan almashtiriladi.",
                   reply_markup={"inline_keyboard": [[{"text": "✅ Ha, tiklash", "callback_data": "zx:ok"},
                                                      {"text": "Bekor qilish", "callback_data": "zx:no"}]]})


async def callback(uid, data, cb):
    if data.startswith("zx:f:"):
        fid = _found.get(data[5:])
        if not fid:
            await bot.answer_cb(cb["id"], "Ro'yxat eskirgan, /zaxiralar ni qayta yuboring", True)
            return
        _pending[uid] = fid
        data = "zx:ok"
    fid = _pending.pop(uid, None)
    await bot.answer_cb(cb["id"])
    if data != "zx:ok" or not fid or not store.is_owner(uid):
        await bot.send(uid, "Bekor qilindi.")
        return
    try:
        ok = store.load_bytes(await bot.download(fid))
    except Exception as e:  # noqa
        ok = False
        log.warning("qo'lda tiklash: %s", e)
    if not ok:
        await bot.send(uid, "❗ Faylni o'qib bo'lmadi, katalog o'zgarmadi.")
        return
    store.settings["owner_id"] = uid
    store.settings["zx_scan"] = 1
    store.settings.pop("backup_msg_id", None)   # yangi zaxira xabari yuborilib, mahkamlanadi
    if hook:
        store.backup_hook = hook
    store.save(backup=False)
    await store.backup_now()
    await bot.send(uid, f"✅ Katalog zaxiradan tiklandi: {_summary(store.s)}.\n"
                        "Yangi zaxira fayli shu chatda mahkamlandi — uni o'chirmang.")


# ---------------- eski zaxiralarni avtomatik topish ----------------
# Har bir zaxira xabari egasining chatida mahkamlanadi. Telegram bot faqat eng oxirgi mahkamlangan xabarni
# ko'radi, shuning uchun mahkamlanganlarni birma-bir vaqtincha olib, ostidagilarini ko'ramiz va keyin
# hammasini avvalgi tartibda qayta mahkamlaymiz.
_found: dict = {}    # "n" -> file_id


def _stats(st):
    ds = [d for d in st.get("designs", {}).values() if not d.get("deleted")]
    items = [it for d in ds for c in d.get("categories", []) for it in c.get("items", [])]
    out = sum(1 for it in items if not it.get("in_stock", True)) + sum(1 for d in ds if d.get("in_stock") is False)
    return len(ds), len(items), out


async def scan(owner):
    pins = []
    try:
        for _ in range(40):
            chat = await bot.call("getChat", chat_id=owner)
            pm = chat.get("pinned_message")
            if not pm:
                break
            pins.append(pm)
            await bot.call("unpinChatMessage", chat_id=owner, message_id=pm["message_id"])
    finally:
        for pm in reversed(pins):        # avvalgi holatiga qaytarish (eng eskisidan boshlab)
            try:
                await bot.call("pinChatMessage", chat_id=owner, message_id=pm["message_id"],
                               disable_notification=True)
            except Exception:  # noqa
                pass
    res = []
    for pm in pins:
        doc = pm.get("document") or {}
        if not str(doc.get("file_name", "")).endswith(".json"):
            continue
        try:
            st = json.loads((await bot.download(doc["file_id"])).decode("utf-8"))
            if "designs" not in st:
                continue
        except Exception:  # noqa
            continue
        res.append({"file_id": doc["file_id"], "saved": float(st.get("saved_at") or pm.get("date") or 0),
                    "stats": _stats(st)})
    res.sort(key=lambda r: -r["saved"])
    return res


async def offer_found(owner, manual=False):
    from datetime import datetime
    import config
    found = await scan(owner)
    cur = _stats(store.s)
    if len(found) < 2 and not manual:
        return
    if not found:
        await bot.send(owner, "Chatda mahkamlangan zaxira fayli topilmadi.")
        return
    _found.clear()
    best = max(range(len(found)), key=lambda i: (found[i]["stats"][1], found[i]["stats"][2], found[i]["saved"]))
    rows, lines = [], []
    for i, r in enumerate(found[:12]):
        _found[str(i)] = r["file_id"]
        t = datetime.fromtimestamp(r["saved"], config.TZ).strftime("%d.%m %H:%M")
        d, n, q = r["stats"]
        star = "⭐ " if i == best else ""
        lines.append(f"{star}{t} — {d} dizayn, {n} mahsulot, ❌ {q} ta qolmadi")
        rows.append([{"text": f"{star}{t} · {n} mahsulot · ❌{q}", "callback_data": f"zx:f:{i}"}])
    rows.append([{"text": "Hozirgisi to'g'ri, tegmang", "callback_data": "zx:no"}])
    await bot.send(owner,
                   "💾 <b>Chatda saqlangan katalog zaxiralari topildi</b>\n\n" + "\n".join(lines) +
                   f"\n\nHozir botda: {cur[0]} dizayn, {cur[1]} mahsulot, ❌ {cur[2]} ta qolmadi.\n\n"
                   "Ma'lumot yo'qolgan bo'lsa — kerakli sanani bosing (⭐ — eng to'liq zaxira). "
                   "Katalog o'sha holatga qaytariladi.",
                   reply_markup={"inline_keyboard": rows}, parse_mode="HTML")
