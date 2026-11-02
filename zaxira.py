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
    store.settings.pop("backup_msg_id", None)   # yangi zaxira xabari yuborilib, mahkamlanadi
    if hook:
        store.backup_hook = hook
    store.save(backup=False)
    await store.backup_now()
    await bot.send(uid, f"✅ Katalog zaxiradan tiklandi: {_summary(store.s)}.\n"
                        "Yangi zaxira fayli shu chatda mahkamlandi — uni o'chirmang.")
