"""Sayt (brauzer) orqali kirish: parol + egasining tasdig'i, hamda kim qachon nimani ko'rgani.

1) Mehmon saytda ism, telefon va umumiy parolni yozadi.
2) Parol to'g'ri bo'lsa, bot egasiga «✅ Ruxsat / ❌ Rad» tugmalari bilan so'rov yuboradi.
3) Ega tasdiqlasa, mehmon 60 kunlik kirish kalitini oladi va katalog ochiladi.
4) Sayt har bir ochilgan dizaynni va «onlayn» belgisini yozib boradi — ega /sayt buyrug'i bilan ko'radi."""
import hashlib
import hmac
import html
import secrets
import time

from aiohttp import web

import config
from store import store
from tg import bot

SESSION_DAYS = 60
DEFAULT_PASSWORD = "lenon55"
_tries: dict = {}     # ip -> [vaqtlar]


def _site() -> dict:
    s = store.s.setdefault("site", {})
    s.setdefault("users", {})
    s.setdefault("password", DEFAULT_PASSWORD)
    return s


def _key() -> bytes:
    return hashlib.sha256(("lenon-sayt2:" + config.BOT_TOKEN).encode()).digest()


def make_token(sid: str) -> str:
    body = f"S2.{sid}.{int(time.time()) + SESSION_DAYS * 86400}"
    return body + "." + hmac.new(_key(), body.encode(), hashlib.sha256).hexdigest()[:32]


def verify_token(tok: str) -> dict | None:
    try:
        body, sig = tok.rsplit(".", 1)
        if not hmac.compare_digest(hmac.new(_key(), body.encode(), hashlib.sha256).hexdigest()[:32], sig):
            return None
        _, sid, exp = body.split(".")
        if int(exp) < time.time():
            return None
        u = _site()["users"].get(sid)
        if not u or u.get("status") != "ok":
            return None
        return {"id": "site:" + sid, "first_name": u.get("name", ""), "site": sid}
    except Exception:  # noqa
        return None


def _ip(request) -> str:
    return (request.headers.get("X-Forwarded-For", "") or request.remote or "").split(",")[0].strip()


def _fmt_ago(t) -> str:
    d = int(time.time() - (t or 0))
    if d < 120:
        return "hozir onlayn 🟢"
    if d < 3600:
        return f"{d // 60} daqiqa oldin"
    if d < 86400:
        return f"{d // 3600} soat oldin"
    return f"{d // 86400} kun oldin"


# ---------------- API ----------------
async def request_access(request):
    ip = _ip(request)
    now = time.time()
    tries = [t for t in _tries.get(ip, []) if now - t < 3600]
    if len(tries) >= 10:
        return web.json_response({"error": "Juda ko'p urinish. 1 soatdan keyin qayta urinib ko'ring."}, status=429)
    _tries[ip] = tries + [now]
    try:
        body = await request.json()
    except Exception:  # noqa
        body = {}
    name = " ".join(str(body.get("name", "")).split())[:40]
    phone = " ".join(str(body.get("phone", "")).split())[:25]
    pw = str(body.get("password", "")).strip()
    if not name or not phone:
        return web.json_response({"error": "Ism va telefon raqamini yozing."}, status=400)
    if not hmac.compare_digest(pw.lower(), str(_site()["password"]).lower()):
        return web.json_response({"error": "Parol noto'g'ri."}, status=403)
    owner = store.settings.get("owner_id")
    if not owner:
        return web.json_response({"error": "Hozircha kirish yopiq."}, status=503)
    sid = secrets.token_hex(6)
    _site()["users"][sid] = {"name": name, "phone": phone, "status": "wait", "created": now,
                             "last_seen": 0, "views": [], "count": 0,
                             "ua": request.headers.get("User-Agent", "")[:120]}
    store.save()
    dev = "📱 telefon" if "Mobile" in _site()["users"][sid]["ua"] else "💻 kompyuter"
    await bot.send(owner, f"🔔 <b>Saytga kirish so'rovi</b>\n\n👤 {html.escape(name)}\n📞 {html.escape(phone)}\n{dev}\n\n"
                          "Shu odamga katalogni ko'rishga ruxsat berasizmi?",
                   reply_markup={"inline_keyboard": [[{"text": "✅ Ruxsat", "callback_data": f"st:ok:{sid}"},
                                                      {"text": "❌ Rad etish", "callback_data": f"st:no:{sid}"}]]})
    return web.json_response({"sid": sid})


async def status(request):
    sid = request.query.get("sid", "")
    u = _site()["users"].get(sid)
    if not u:
        return web.json_response({"status": "none"})
    if u["status"] == "ok":
        return web.json_response({"status": "ok", "token": make_token(sid)})
    return web.json_response({"status": u["status"]})


async def track(request):
    from webapi import verify_init_data
    user = verify_init_data(request.headers.get("X-Init-Data", ""))
    if not user or not user.get("site"):
        return web.json_response({"ok": False})
    u = _site()["users"].get(user["site"])
    try:
        body = await request.json()
    except Exception:  # noqa
        body = {}
    now = time.time()
    u["last_seen"] = now
    view = str(body.get("view", ""))[:60]
    if view and (not u["views"] or u["views"][-1][1] != view or now - u["views"][-1][0] > 600):
        u["views"].append([now, view])
        u["views"] = u["views"][-60:]
        u["count"] = u.get("count", 0) + 1
        store.save(backup=False)
    return web.json_response({"ok": True})


def routes(app):
    app.router.add_post("/api/site/request", request_access)
    app.router.add_get("/api/site/status", status)
    app.router.add_post("/api/site/track", track)


# ---------------- bot (egasi uchun) ----------------
async def callback(uid, data, cb):
    _, act, sid = (data.split(":") + ["", ""])[:3]
    u = _site()["users"].get(sid)
    if not u:
        await bot.answer_cb(cb["id"], "So'rov topilmadi", True)
        return
    u["status"] = "ok" if act == "ok" else "no"
    store.save()
    await bot.answer_cb(cb["id"], "Ruxsat berildi ✅" if act == "ok" else "Rad etildi")
    txt = (f"{'✅ Ruxsat berildi' if act == 'ok' else '❌ Rad etildi'}: {html.escape(u['name'])} ({html.escape(u['phone'])})")
    try:
        await bot.call("editMessageText", chat_id=cb["message"]["chat"]["id"],
                       message_id=cb["message"]["message_id"], text=txt, parse_mode="HTML")
    except Exception:  # noqa
        await bot.send(uid, txt)


async def report(uid, arg=""):
    s = _site()
    if arg.startswith("parol"):
        new = arg[5:].strip()
        if len(new) < 4:
            await bot.send(uid, "Yangi parolni shunday yozing: <code>/sayt parol yangiparol</code> (kamida 4 belgi)")
            return
        s["password"] = new
        store.save()
        await bot.send(uid, f"🔑 Sayt paroli o'zgartirildi: <code>{html.escape(new)}</code>\n"
                            "Oldin ruxsat olganlar kirishda davom etadi.")
        return
    users = sorted(s["users"].items(), key=lambda kv: -(kv[1].get("last_seen") or kv[1].get("created") or 0))
    ok = [(k, u) for k, u in users if u.get("status") == "ok"]
    wait = [(k, u) for k, u in users if u.get("status") == "wait"]
    online = [u for _, u in ok if time.time() - (u.get("last_seen") or 0) < 120]
    lines = [f"🌐 <b>Sayt</b>: {config.PUBLIC_URL}/sayt",
             f"🔑 Parol: <code>{html.escape(s['password'])}</code>  (o'zgartirish: <code>/sayt parol yangisi</code>)",
             f"🟢 Hozir onlayn: <b>{len(online)}</b> · ruxsat berilganlar: {len(ok)} · kutayotganlar: {len(wait)}", ""]
    rows = []
    for sid, u in ok[:25]:
        last = [v for _, v in u.get("views", [])[-5:]]
        lines.append(f"👤 <b>{html.escape(u['name'])}</b> · {html.escape(u['phone'])} · {_fmt_ago(u.get('last_seen'))}"
                     + (f"\n    👁 {html.escape(', '.join(last))}" if last else "")
                     + f"\n    jami {u.get('count', 0)} ta ko'rish")
        rows.append([{"text": f"🚫 {u['name'][:20]} — kirishni yopish", "callback_data": f"st:no:{sid}"}])
    for sid, u in wait[:10]:
        rows.append([{"text": f"✅ {u['name'][:18]}", "callback_data": f"st:ok:{sid}"},
                     {"text": "❌ Rad", "callback_data": f"st:no:{sid}"}])
    if not ok:
        lines.append("Hali hech kim saytga kirmagan.")
    await bot.send(uid, "\n".join(lines)[:4000], reply_markup={"inline_keyboard": rows[:40]} if rows else None)
