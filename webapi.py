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


def is_private() -> bool:
    return store.settings.get("private", True)


async def catalog(request):
    if is_private():
        user = verify_init_data(request.headers.get("X-Init-Data", ""))
        import admin as _admin
        if not user or not await _admin.is_member(user.get("id")):
            return web.json_response({"private": True}, status=403)
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
                                  price=body.get("price"), category=body.get("category"),
                                  size=body.get("size"))
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
        d = store.designs[design_id]
        _, it = store.find_item(d, item_id)
        label = ops.item_label(d, it) if it else None
    except Exception:  # noqa
        label = None
    try:
        out = await asyncio.get_event_loop().run_in_executor(None, images.process, raw, True, label)
    except Exception as e:  # noqa
        import checker
        await checker.report_error(f"Mini ilova: rasm o'qilmadi ({len(raw)} bayt, {raw[:12]!r})", e)
        return web.json_response({"ok": False, "error": "Rasmni o'qib bo'lmadi. Rasmni JPG/PNG qilib qayta yuboring "
                                                        "yoki botning o'ziga yuboring."}, status=400)
    owner = store.settings.get("owner_id") or user["id"]
    m = await bot.call("sendPhoto", files={"photo": ("rasm.jpg", out, "image/jpeg")},
                       chat_id=str(owner), disable_notification="true")
    fid = m["photo"][-1]["file_id"]
    await bot.safe_delete(owner, m["message_id"])
    raw_fid = None
    try:  # asl rasmni ham saqlab qo'yamiz — keyin qayta tayyorlash uchun
        m2 = await bot.call("sendDocument", files={"document": ("asl.jpg", raw, "image/jpeg")},
                            chat_id=str(owner), disable_notification="true", disable_content_type_detection="true")
        raw_fid = m2["document"]["file_id"]
        await bot.safe_delete(owner, m2["message_id"])
    except Exception:  # noqa
        pass
    try:
        ops.set_item_photo(design_id, item_id, fid, raw=raw_fid)
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


_pdf_busy: set = set()


async def make_pdf(design_id: str) -> tuple[bytes, str]:
    """Dizayn katalogini PDF qiladi. Qaytaradi: (pdf, fayl nomi)."""
    import pdfgen
    import publisher
    d = store.designs[design_id]
    sec = store.section(d["section"])
    fids = {it["id"]: it["photo"] for c in d.get("categories", []) for it in c["items"] if it.get("photo")}
    sem = asyncio.Semaphore(6)
    photos = {}

    async def get(iid, fid):
        async with sem:
            try:
                photos[iid] = await bot.download(fid)
            except Exception:  # noqa
                pass
    await asyncio.gather(*(get(i, f) for i, f in fids.items()))
    cover = None
    if d.get("photos"):
        try:
            cover = await bot.download(d["photos"][0])
        except Exception:  # noqa
            pass
    view = {"title": d["title"], "categories": [
        {"name": c["name"], "items": [dict(it, size=ops.item_size(d, it)) for it in c["items"]]}
        for c in d.get("categories", [])]}
    pdf = await asyncio.get_event_loop().run_in_executor(
        None, lambda: pdfgen.build(view, sec["title"], photos, cover, publisher.item_state))
    name = "" + "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in d["title"]) + ".pdf"
    return pdf, name


async def pdf_export(request):
    """Mini ilovadagi «PDF» tugmasi: dizayn katalogini PDF qilib, foydalanuvchining Telegram chatiga yuboradi."""
    user = verify_init_data(request.headers.get("X-Init-Data", ""))
    if not user or not store.is_admin(user.get("id")):
        return web.json_response({"ok": False, "error": "Ruxsat yo'q"}, status=403)
    body = await request.json()
    did = body.get("design")
    if did not in store.designs:
        return web.json_response({"ok": False, "error": "Dizayn topilmadi"}, status=404)
    key = (user["id"], did)
    if key in _pdf_busy:
        return web.json_response({"ok": False, "error": "PDF tayyorlanmoqda, biroz kuting"}, status=429)
    _pdf_busy.add(key)
    try:
        pdf, name = await make_pdf(did)
        d = store.designs[did]
        await bot.call("sendDocument", files={"document": (name, pdf, "application/pdf")}, chat_id=str(user["id"]),
                       caption=f"📄 {d['title']} — katalog (PDF)")
    except Exception as e:  # noqa
        desc = str(getattr(e, "description", e))
        if "blocked" in desc or "chat not found" in desc or "initiate" in desc:
            return web.json_response({"ok": False, "error": "Avval botni oching va /start bosing, keyin qayta urining"},
                                     status=400)
        import checker
        await checker.report_error("PDF tayyorlanmadi", e)
        return web.json_response({"ok": False, "error": "PDF tayyorlab bo'lmadi"}, status=500)
    finally:
        _pdf_busy.discard(key)
    return web.json_response({"ok": True})


def routes(app: web.Application):
    app.router.add_get("/app", page)
    app.router.add_get("/app/", page)
    app.router.add_get("/api/catalog", catalog)
    app.router.add_get("/api/me", me)
    app.router.add_post("/api/admin", admin_action)
    app.router.add_post("/api/admin/photo", admin_photo)
    app.router.add_post("/api/pdf", pdf_export)
    app.router.add_get("/api/admin/info", admin_info)
    app.router.add_get("/img/{fid}", img)
    app.router.add_get("/static/logo.png", lambda r: web.FileResponse(
        config.ASSETS / "logo.png", headers={"Cache-Control": "public, max-age=86400"}))
