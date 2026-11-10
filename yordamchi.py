"""Sun'iy intellekt yordamchisi (Claude) — egasi botga oddiy so'z bilan yozadi, bot tushunib bajaradi.

Xavfsizlik:
  • Faqat bot EGASI uchun ishlaydi (boshqalar yozsa — javob bermaydi).
  • Katalogni O'ZGARTIRADIGAN har bir amal (qolmadi/keldi, narx, iBox'ga yuklash) avval
    «✅ Bajarish / ❌ Bekor» tugmasi bilan so'raladi — egasi bosmaguncha hech narsa o'zgarmaydi.
  • Istalgan payt o'chiriladi: /ai_ochir (yoqish: /ai_yoq). Kalit olib tashlansa ham ishlamaydi.
Sozlash: Render → Environment → ANTHROPIC_API_KEY. Model: AI_MODEL (standart claude-haiku-5-5)."""
import asyncio
import html
import json
import logging
import os
import re
import secrets
import time

from aiohttp import ClientSession, ClientTimeout

import config
from store import store
from tg import bot

log = logging.getLogger("yordamchi")
KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
MODEL = os.getenv("AI_MODEL", "claude-haiku-5-5").strip()
_hist: dict = {}          # uid -> [messages]
_pending: dict = {}       # token -> {"uid", "actions", "exp"}
MAX_TURNS = 6

SYSTEM = """Sen LENON chinni (farfor idishlar) do'koni katalog botining yordamchisisan. Egasi bilan o'zbek tilida,
qisqa va aniq gaplash. Katalog: bo'limlar (LENON, BODEN, naborlar), dizaynlar (masalan "LENON 10-IL"),
ularda mahsulotlar (nom, kod, narx, holat: bor / qolmadi / keldi).
Qoidalar:
- Avval kerakli ma'lumotni asboblar (tools) bilan top: qidir, dizaynlar, qolmaganlar.
- O'zgartirish kerak bo'lsa — taklif_qil asbobi bilan amallar ro'yxatini yubor; egasi tugmani bosgandagina bajariladi.
  Hech qachon "bajardim" dema — "tasdiqlang" de.
- Mahsulot aniq topilmasa yoki bir nechta mos kelsa — egasidan aniqlashtirib so'ra.
- Narxlar dollarda ($). Javoblarda ortiqcha so'z ishlatma."""

TOOLS = [
    {"name": "qidir", "description": "Katalogdan mahsulot qidirish (nom yoki kod bo'yicha, dizayn nomini ham qo'shish mumkin).",
     "input_schema": {"type": "object", "properties": {"soz": {"type": "string"}, "dizayn": {"type": "string"}},
                      "required": ["soz"]}},
    {"name": "dizaynlar", "description": "Barcha dizaynlar ro'yxati (bo'lim, nechta mahsulot, nechta rasm).",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "qolmaganlar", "description": "Hozir 'qolmadi' belgilangan mahsulotlar ro'yxati.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "taklif_qil",
     "description": "O'zgarishlarni egasiga tasdiqlash uchun yuborish. amallar: [{tur: 'qolmadi'|'keldi'|'narx'|'iboxga', "
                    "design_id, item_id (iboxga uchun shart emas), narx (faqat 'narx' uchun, masalan '4.5$')}]. "
                    "design_id va item_id faqat qidir natijasidan olinsin.",
     "input_schema": {"type": "object", "properties": {
         "izoh": {"type": "string"},
         "amallar": {"type": "array", "items": {"type": "object", "properties": {
             "tur": {"type": "string", "enum": ["qolmadi", "keldi", "narx", "iboxga"]},
             "design_id": {"type": "string"}, "item_id": {"type": "string"}, "narx": {"type": "string"}},
             "required": ["tur", "design_id"]}}},
         "required": ["amallar"]}},
]


def enabled() -> bool:
    return bool(KEY) and not config.ROTATION and store.settings.get("ai_on", True)


def _n(s):
    return re.sub(r"[^0-9a-z]+", "", str(s or "").lower())


def _state(it):
    import publisher
    return {"qolmadi": "qolmadi", "keldi": "keldi"}.get(publisher.item_state(it), "bor")


# ---------------- asboblar ----------------
def t_qidir(soz, dizayn=""):
    q, dq = _n(soz), _n(dizayn)
    out = []
    for d in store.designs.values():
        if d.get("deleted") or (dq and dq not in _n(d["title"])):
            continue
        for c in d["categories"]:
            for it in c["items"]:
                if q and (q in _n(it["name"]) or q == _n(it.get("code")) or q in _n(d["title"] + it["name"])):
                    out.append({"design_id": d["id"], "dizayn": d["title"], "item_id": it["id"], "nomi": it["name"],
                                "kodi": it.get("code", ""), "narxi": it.get("price", ""), "holati": _state(it),
                                "rasm": bool(it.get("photo"))})
    return {"topildi": len(out), "natija": out[:40]}


def t_dizaynlar():
    res = []
    for d in store.designs.values():
        if d.get("deleted"):
            continue
        items = [it for c in d["categories"] for it in c["items"]]
        res.append({"design_id": d["id"], "nomi": d["title"], "bolim": d.get("section"),
                    "mahsulot": len(items), "rasmli": sum(1 for it in items if it.get("photo"))})
    return {"dizaynlar": res}


def t_qolmaganlar():
    out = []
    for d in store.designs.values():
        if d.get("deleted"):
            continue
        for c in d["categories"]:
            for it in c["items"]:
                if _state(it) == "qolmadi":
                    out.append(f"{d['title']}: {it['name']} ({it.get('code', '')})")
    return {"soni": len(out), "royxat": out[:150]}


def _describe(a):
    d = store.designs.get(a.get("design_id"))
    if not d:
        return None
    if a["tur"] == "iboxga":
        return f"🖼 {d['title']}: rasmlarni iBox'ga yuklash"
    _, it = store.find_item(d, a.get("item_id"))
    if not it:
        return None
    if a["tur"] == "narx":
        return f"💲 {it['name']} ({d['title']}): {it.get('price')} → {a.get('narx')}"
    return f"{'❌ Qolmadi' if a['tur'] == 'qolmadi' else '✅ Keldi'}: {it['name']} ({d['title']})"


async def t_taklif(uid, amallar, izoh=""):
    good = [(a, _describe(a)) for a in amallar]
    good = [(a, txt) for a, txt in good if txt]
    if not good:
        return {"xato": "amallar noto'g'ri — design_id/item_id ni qidir natijasidan oling"}
    tok = secrets.token_hex(4)
    _pending[tok] = {"uid": uid, "actions": [a for a, _ in good], "exp": time.time() + 3600}
    text = (html.escape(izoh) + "\n\n" if izoh else "") + "<b>Tasdiqlaysizmi?</b>\n" + \
        "\n".join(html.escape(t) for _, t in good[:40])
    await bot.send(uid, text[:4000], reply_markup={"inline_keyboard": [[
        {"text": "✅ Bajarish", "callback_data": f"ai:ok:{tok}"}, {"text": "❌ Bekor", "callback_data": f"ai:no:{tok}"}]]})
    return {"holat": f"{len(good)} ta amal egasiga tasdiqlash uchun yuborildi"}


async def _run_tool(uid, name, inp):
    try:
        if name == "qidir":
            return t_qidir(inp.get("soz", ""), inp.get("dizayn", ""))
        if name == "dizaynlar":
            return t_dizaynlar()
        if name == "qolmaganlar":
            return t_qolmaganlar()
        if name == "taklif_qil":
            return await t_taklif(uid, inp.get("amallar") or [], inp.get("izoh", ""))
    except Exception as e:  # noqa
        return {"xato": str(e)[:200]}
    return {"xato": "noma'lum asbob"}


# ---------------- Claude bilan suhbat ----------------
async def _call(messages):
    body = {"model": MODEL, "max_tokens": 1200, "system": SYSTEM, "tools": TOOLS, "messages": messages}
    async with ClientSession(timeout=ClientTimeout(total=90)) as s:
        async with s.post("https://api.anthropic.com/v1/messages", json=body,
                          headers={"x-api-key": KEY, "anthropic-version": "2023-06-01",
                                   "content-type": "application/json"}) as r:
            j = await r.json(content_type=None)
            if r.status >= 400:
                raise RuntimeError(((j or {}).get("error") or {}).get("message") or f"HTTP {r.status}")
            return j


async def handle(uid, text) -> bool:
    """Egasining oddiy matnli xabari. True — yordamchi javob berdi."""
    if not enabled() or not store.is_owner(uid):
        return False
    msgs = _hist.setdefault(uid, [])
    msgs.append({"role": "user", "content": text[:2000]})
    del msgs[:-16]
    while msgs and (msgs[0]["role"] != "user" or not isinstance(msgs[0]["content"], str)):
        msgs.pop(0)
    try:
        await bot.call("sendChatAction", chat_id=uid, action="typing")
    except Exception:  # noqa
        pass
    try:
        for _ in range(MAX_TURNS):
            j = await _call(msgs)
            content = j.get("content") or []
            msgs.append({"role": "assistant", "content": content})
            uses = [c for c in content if c.get("type") == "tool_use"]
            if not uses:
                txt = "\n".join(c.get("text", "") for c in content if c.get("type") == "text").strip()
                if txt:
                    await bot.send(uid, html.escape(txt)[:4000])
                return True
            results = []
            for u in uses:
                res = await _run_tool(uid, u["name"], u.get("input") or {})
                results.append({"type": "tool_result", "tool_use_id": u["id"],
                                "content": json.dumps(res, ensure_ascii=False)[:12000]})
            msgs.append({"role": "user", "content": results})
        await bot.send(uid, "Savol juda murakkab bo'ldi, qisqaroq qilib yozing.")
    except Exception as e:  # noqa
        log.warning("yordamchi: %s", e)
        _hist.pop(uid, None)
        await bot.send(uid, f"⚠️ Yordamchi javob bera olmadi: {html.escape(str(e))[:200]}")
    return True


async def callback(uid, data, cb):
    import ops
    import publisher
    _, act, tok = (data.split(":") + ["", ""])[:3]
    p = _pending.pop(tok, None)
    await bot.answer_cb(cb["id"])
    if not p or p["uid"] != uid or p["exp"] < time.time() or not store.is_owner(uid):
        await bot.send(uid, "Bu taklif eskirgan.")
        return
    if act != "ok":
        await bot.send(uid, "Bekor qilindi.")
        return
    done, errs = 0, []
    for a in p["actions"]:
        try:
            if a["tur"] in ("qolmadi", "keldi"):
                ops.set_stock(a["design_id"], a.get("item_id"), a["tur"] == "keldi")
            elif a["tur"] == "narx":
                ops.edit_item(a["design_id"], a["item_id"], price=a.get("narx"))
                publisher.queue_update(a["design_id"])
            elif a["tur"] == "iboxga":
                import iboxrasm
                d = store.designs[a["design_id"]]
                asyncio.get_event_loop().create_task(iboxrasm.push_design(uid, d["title"]))
            done += 1
        except Exception as e:  # noqa
            errs.append(str(e)[:100])
    msg = f"✅ Bajarildi: {done} ta amal."
    if errs:
        msg += "\n⚠️ " + "\n".join(html.escape(x) for x in errs[:10])
    await bot.send(uid, msg)
    h = _hist.get(uid)
    if h is not None:
        h.append({"role": "user", "content": f"[Egasi tasdiqladi: {done} ta amal bajarildi]"})
        h.append({"role": "assistant", "content": [{"type": "text", "text": "Yaxshi, bajarildi."}]})


async def toggle(uid, on: bool):
    if not store.is_owner(uid):
        return
    store.settings["ai_on"] = on
    store.save()
    if on and not KEY:
        await bot.send(uid, "🤖 Yoqildi, lekin kalit yo'q: Render → Environment ga <code>ANTHROPIC_API_KEY</code> qo'shing.")
        return
    await bot.send(uid, "🤖 Yordamchi <b>yoqildi</b>. Menga oddiy so'z bilan yozing." if on else
                   "🤖 Yordamchi <b>o'chirildi</b>. Qayta yoqish: /ai_yoq")
