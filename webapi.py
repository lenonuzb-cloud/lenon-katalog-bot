"""Katalog mini ilovasi uchun veb-server qismi (API va rasmlar)."""
import asyncio
import hashlib
import hmac
import json
import logging
import time
from urllib.parse import parse_qsl

from aiohttp import web

import config
import images
import ops
from store import store
from tg import bot

log = logging.getLogger("webapi")

_img_locks: dict[str, asyncio.Lock] = {}


def verify_init_data(init_data: str) -> dict | None:
    """Telegram Mini App'dan kelgan initData imzosini tekshiradi. To'g'ri bo'lsa foydalanuvchini qaytaradi."""
    if not init_data:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        recv_hash = pairs.pop("hash", "")
        check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, recv_hash):
            return None
        if time.time() - int(pairs.get("auth_date", "0")) > 7 * 86400:
            return None
        return json.loads(pairs.get("user", "{}"))
    except Exception:  # noqa
        return None


async def page(request):
    resp = web.FileResponse(config.WEBAPP_DIR / "index.html")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


async def catalog(request):
    data = store.public_catalog()
    data["bot"] = bot.username
    return web.json_response(data, headers={"Cache-Control": "no-cache"})


async def me(request):
    user = verify_init_data(request.headers.get("X-Init-Data", ""))
    return web.json_response({"admin": bool(user and store.is_admin(user.get("id"))),
                              "name": (user or {}).get("first_name", "")})


async def img(request):
    fid = request.match_info["fid"]
    try:
        w = int(request.query.get("w", "800"))
    except ValueError:
        w = 800
    w = max(120, min(w, 1280))
    key = hashlib.sha1(f"{fid}:{w}".encode()).hexdigest()
    path = config.CACHE_DIR / f"{key}.jpg"
    if not path.exists():
        lock = _img_locks.setdefault(key, asyncio.Lock())
        async with lock:
            if not path.exists():
                known = store.known_photo(fid)
                if not known:
                    raise web.HTTPNotFound()
                try:
                    data = await bot.download(fid)
                except Exception:  # noqa
                    raise web.HTTPNotFound()
                out = await asyncio.get_event_loop().run_in_executor(None, images.thumbnail, data, w)
                path.write_bytes(out)
    return web.FileResponse(path, headers={"Cache-Control": "public, max-age=604800"})


async def admin_action(request):
    user = verify_init_data(request.headers.get("X-Init-Data", ""))
    if not user or not store.is_admin(user.get("id")):
        return web.json_response({"ok": False, "error": "Ruxsat yo'q"}, status=403)
    try:
        body = await request.json()
    except Exception:  # noqa
        return web.json_response({"ok": False, "error": "Noto'g'ri so'rov"}, status=400)
    a = body.get("action")
    warns = []
    try:
        if a == "stock":
            ops.set_stock(body["design"], body.get("item"), bool(body["in_stock"]))
        elif a == "edit_item":
            warns = ops.edit_item(body["design"], body["item"], name=body.get("name"), code=body.get("code"),
                                  price=body.get("price"), category=body.get("category"))
        elif a == "add_item":
            _, warns = ops.add_item(body["design"], body.get("category", ""), body.get("name", ""),
                                    body.get("code", ""), body.get("price", ""))
        elif a == "add_items":
            n, warns = ops.add_items(body["design"], body.get("items") or [])
            if not n and not warns:
                return web.json_response({"ok": False, "error": "Hech narsa tanlanmadi"}, status=400)
        elif a == "add_design":
            did = ops.add_design(body["section"], body.get("title", ""))
            return web.json_response({"ok": True, "warnings": [], "design": did, "catalog": store.public_catalog()})
        elif a == "reorder":
            ops.reorder(body["design"], body.get("layout") or [])
        elif a == "delete_item":
            ops.delete_item(body["design"], body["item"])
        elif a == "rename":
            ops.rename_design(body["design"], body.get("title", ""))
        elif a == "delete_design":
            ops.delete_design(body["design"])
        else:
            return web.json_response({"ok": False, "error": "Noma'lum amal"}, status=400)
    except ops.OpError as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)
    except KeyError as e:
        return web.json_response({"ok": False, "error": f"Maydon yetishmaydi: {e}"}, status=400)
    return web.json_response({"ok": True, "warnings": warns, "catalog": store.public_catalog()})


async def admin_photo(request):
    """Mini ilovadan mahsulot rasmini yuklash: tahrirlash -> logo -> Telegram'ga saqlash."""
    user = verify_init_data(request.headers.get("X-Init-Data", ""))
    if not user or not store.is_admin(user.get("id")):
        return web.json_response({"ok": False, "error": "Ruxsat yo'q"}, status=403)
    form = await request.post()
    design_id, item_id = form.get("design"), form.get("item")
    f = form.get("file")
    if not design_id or not item_id or f is None or not hasattr(f, "file"):
        return web.json_response({"ok": False, "error": "Rasm topilmadi"}, status=400)
    raw = f.file.read()
    try:
        out = await asyncio.get_event_loop().run_in_executor(None, images.process, raw, True)
    except Exception:  # noqa
        return web.json_response({"ok": False, "error": "Rasmni o'qib bo'lmadi"}, status=400)
    owner = store.settings.get("owner_id") or user["id"]
    m = await bot.call("sendPhoto", files={"photo": ("rasm.jpg", out, "image/jpeg")},
                       chat_id=str(owner), disable_notification="true")
    fid = m["photo"][-1]["file_id"]
    await bot.safe_delete(owner, m["message_id"])
    try:
        ops.set_item_photo(design_id, item_id, fid)
    except ops.OpError as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)
    return web.json_response({"ok": True, "catalog": store.public_catalog()})


async def admin_info(request):
    """Mini ilova uchun hisobot va nazoratchi tekshiruvi."""
    user = verify_init_data(request.headers.get("X-Init-Data", ""))
    if not user or not store.is_admin(user.get("id")):
        return web.json_response({"ok": False, "error": "Ruxsat yo'q"}, status=403)
    import checker
    import report
    kind = request.query.get("t", "report")
    if kind == "check":
        sc = await checker.self_check()
        probs = checker.audit()
        return web.json_response({"ok": True, "bot": sc, "problems": probs})
    try:
        h = int(request.query.get("h", "24"))
    except ValueError:
        h = 24
    return web.json_response({"ok": True, "html": await report.build(max(1, min(h, 24 * 90)))})


def routes(app: web.Application):
    app.router.add_get("/app", page)
    app.router.add_get("/app/", page)
    app.router.add_get("/api/catalog", catalog)
    app.router.add_get("/api/me", me)
    app.router.add_post("/api/admin", admin_action)
    app.router.add_post("/api/admin/photo", admin_photo)
    app.router.add_get("/api/admin/info", admin_info)
    app.router.add_get("/img/{fid}", img)
    app.router.add_get("/static/logo.png", lambda r: web.FileResponse(
        config.ASSETS / "logo.png", headers={"Cache-Control": "public, max-age=86400"}))
