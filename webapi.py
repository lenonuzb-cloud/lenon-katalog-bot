"""Katalog mini ilovasi uchun veb-server qismi (API va rasmlar)."""
import asyncio
import io
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


_page_cache = {}


async def page(request):
    html = _page_cache.get("html")
    if html is None:
        html = (config.WEBAPP_DIR / "index.html").read_text("utf-8")
        p = config.URL_PREFIX
        if p:  # ikkinchi bot /uz yo'lida ishlaydi — ichki manzillarni moslaymiz
            html = (html.replace('"/api/', f'"{p}/api/').replace("`/img/", f"`{p}/img/")
                    .replace('"/static/', f'"{p}/static/'))
        if config.LENONUZ:
            html = html.replace("<b>Lenon 55</b> guruhi", "<b>LENON_UZ</b> guruhi")
        _page_cache["html"] = html
    resp = web.Response(body=html.encode("utf-8"), content_type="text/html",
                        headers={"Cache-Control": "no-cache"})
    resp.enable_compression()  # sekin internetda tezroq
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
    resp = web.json_response(data, headers={"Cache-Control": "no-cache"})
    resp.enable_compression()  # ~200 KB -> ~25 KB
    return resp


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
            d_ = store.designs.get(body["design"]) or {}
            whole = (store.section(d_.get("section")) or {}).get("whole")
            ops.set_stock(body["design"], None if whole else body.get("item"), bool(body["in_stock"]))
        elif a == "edit_item":
            warns = ops.edit_item(body["design"], body["item"], name=body.get("name"), code=body.get("code"),
                                  price=body.get("price"), category=body.get("category"),
                                  size=body.get("size"))
        elif a == "week_set":
            import schedule
            schedule.set_day(int(body["day"]), list(body.get("ids") or []))
        elif a == "post_now":
            if not config.ROTATION:
                return web.json_response({"ok": False, "error": "Bu guruhda ishlamaydi"}, status=400)
            import rotation
            await rotation.post_now(body["design"])
        elif a == "remove_product":
            ops.remove_product(body["section"], body.get("name", ""), body.get("code", ""),
                               "hide" if body.get("mode") == "hide" else "all")
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
    if kind == "week":
        import schedule
        return web.json_response({"ok": True, "week": schedule.week(), "summary": schedule.summary(),
                                  "days": schedule.DAYS, "short": schedule.SHORT})
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
        for c in d.get("categories", [])],
        "agents": [(a, store.settings["agents"].get(a, "")) for a in d.get("agents", [])]}
    pdf = await asyncio.get_event_loop().run_in_executor(
        None, lambda: pdfgen.build(view, sec["title"], photos, cover, publisher.item_state))
    name = "" + "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in d["title"]) + ".pdf"
    return pdf, name


async def send_design_photos(chat_id, design_id) -> int:
    """Dizayndagi barcha rasmlarni (dizayn rasmlari + mahsulot rasmlari) oddiy rasm qilib albomlarda yuboradi."""
    d = store.designs[design_id]
    media = [{"type": "photo", "media": f, "caption": d["title"]} for f in d.get("photos", [])]
    for c in d.get("categories", []):
        for it in c["items"]:
            if it.get("photo"):
                cap = " · ".join(x for x in (it["name"], it.get("code", ""), it.get("price", "")) if x)
                media.append({"type": "photo", "media": it["photo"], "caption": cap})
    if not media:
        return 0
    for i in range(0, len(media), 10):
        chunk = media[i:i + 10]
        if len(chunk) == 1:
            m = chunk[0]
            await bot.call("sendPhoto", chat_id=chat_id, photo=m["media"], caption=m["caption"])
        else:
            await bot.call("sendMediaGroup", chat_id=chat_id, media=chunk)
        await asyncio.sleep(1.2)
    return len(media)


async def photos_zip(design_id) -> tuple[bytes, str, int]:
    """Dizayn mahsulot rasmlari — bitta ZIP, har bir rasm KOD bilan nomlangan (iBox va boshqa dasturlarga qo'yish uchun)."""
    import re as _re
    import zipfile
    d = store.designs[design_id]
    buf = io.BytesIO()
    n = 0
    used = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for c in d.get("categories", []):
            for it in c["items"]:
                if not it.get("photo"):
                    continue
                try:
                    data = await bot.download(it["photo"])
                except Exception:  # noqa
                    continue
                base = " - ".join(x for x in (it.get("code", ""), it["name"]) if x)
                base = _re.sub(r'[\\/:*?"<>|]+', "_", base).strip() or it["id"]
                fn, k = base, 2
                while fn in used:
                    fn, k = f"{base} ({k})", k + 1
                used.add(fn)
                z.writestr(f"{fn}.jpg", data)
                n += 1
    name = "LENON_" + "".join(ch if ch.isalnum() or ch in "-_" else "_"
                              for ch in d["title"].replace("LENON ", "")) + "_rasmlar.zip"
    return buf.getvalue(), name, n


async def photos_export(request):
    """Mini ilovadagi «🖼 Rasmlar» tugmasi: dizayn rasmlarini bot chatiga oddiy rasm qilib yuboradi."""
    user = verify_init_data(request.headers.get("X-Init-Data", ""))
    if not user or not store.is_admin(user.get("id")):
        return web.json_response({"ok": False, "error": "Ruxsat yo'q"}, status=403)
    did = (await request.json()).get("design")
    if did not in store.designs:
        return web.json_response({"ok": False, "error": "Dizayn topilmadi"}, status=404)
    try:
        n = await send_design_photos(user["id"], did)
    except Exception as e:  # noqa
        import checker
        await checker.report_error("Rasmlar yuborilmadi", e)
        return web.json_response({"ok": False, "error": "Rasmlarni yuborib bo'lmadi"}, status=500)
    if not n:
        return web.json_response({"ok": False, "error": "Bu dizaynda hali rasm yo'q"}, status=400)
    return web.json_response({"ok": True, "count": n})


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
        pdf, name = await cached_pdf(did)
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


async def product_page(request):
    """Jadvaldagi mahsulot nomi bosilganda — faqat shu mahsulot rasmi (internet sekin joyda ham tez ochiladi)."""
    import html as _h
    import publisher
    d = store.designs.get(request.match_info["did"])
    it = None
    if d and not d.get("deleted"):
        _, it = store.find_item(d, request.match_info["iid"])
    if not it:
        raise web.HTTPNotFound(text="Mahsulot topilmadi")
    e = _h.escape
    st = publisher.item_state(it)
    badge = {"qolmadi": '<span class="b r">QOLMADI</span>', "keldi": '<span class="b g">KELDI</span>',
             "new": '<span class="b r">NEW</span>'}.get(st, "")
    size = ops.item_size(d, it)
    meta = " · ".join(x for x in (it.get("code", ""), size) if x)
    photo = (f'<img src="{config.URL_PREFIX}/img/{e(it["photo"])}?w=1080" alt="{e(it["name"])}">' if it.get("photo")
             else '<div class="no">Rasm hali qo\'yilmagan</div>')
    cat = f"https://t.me/{bot.username}?startapp=i_{d['id']}_{it['id']}" if bot.username else ""
    page_html = f"""<!doctype html><html lang="uz"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(it["name"])} — LENON</title>
<style>body{{margin:0;background:#111;color:#eee;font:16px/1.4 system-ui,sans-serif}}
.w{{max-width:720px;margin:0 auto}}img{{width:100%;display:block}}
.no{{aspect-ratio:1;display:flex;align-items:center;justify-content:center;color:#888;background:#1c1c1c}}
.i{{padding:14px 16px 24px}}h1{{margin:0 0 4px;font-size:22px;color:#fff}}.m{{color:#aaa;font-size:14px}}
.p{{color:#d6a31e;font-size:24px;font-weight:700;margin-top:8px}}.d{{color:#888;font-size:13px;margin-top:4px}}
.b{{display:inline-block;padding:3px 10px;border-radius:12px;font-size:12px;font-weight:700;margin-left:8px;vertical-align:middle}}
.r{{background:#cd2828;color:#fff}}.g{{background:#1e9646;color:#fff}}
a{{display:inline-block;margin-top:16px;color:#d6a31e;text-decoration:none;border:1px solid #d6a31e;border-radius:20px;padding:8px 16px;font-size:14px}}</style>
</head><body><div class="w">{photo}<div class="i"><h1>{e(it["name"])}{badge}</h1>
<div class="m">{e(meta)}</div><div class="p">{e(it.get("price", ""))}</div><div class="d">{e(d["title"])}</div>
{f'<a href="{e(cat)}">📒 Katalogda ochish</a>' if cat else ""}</div></div></body></html>"""
    return web.Response(text=page_html, content_type="text/html", headers={"Cache-Control": "no-cache"})


_pdf_lock = asyncio.Lock()   # bir vaqtda bitta PDF (bepul serverda xotira kam)


def _pdf_sig(d) -> str:
    agents = [(a, store.settings["agents"].get(a, "")) for a in d.get("agents", [])]
    return hashlib.md5(json.dumps({"c": d.get("categories"), "p": d.get("photos"), "t": d["title"], "a": agents,
                                   "v": 4}, sort_keys=True, default=str).encode()).hexdigest()[:16]


async def cached_pdf(did) -> tuple[bytes, str]:
    """Dizayn PDF i — jadval o'zgarmagan bo'lsa diskdagi tayyor nusxa, o'zgargan bo'lsa yangisi."""
    d = store.designs[did]
    path = config.CACHE_DIR / f"pdf_{did}_{_pdf_sig(d)}.pdf"
    name = "LENON_" + "".join(ch if ch.isalnum() or ch in "-_" else "_"
                              for ch in d["title"].replace("LENON ", "")) + ".pdf"
    if path.exists():
        return path.read_bytes(), name
    async with _pdf_lock:
        if not path.exists():
            pdf, name = await make_pdf(did)
            for old in config.CACHE_DIR.glob(f"pdf_{did}_*.pdf"):
                old.unlink(missing_ok=True)
            path.write_bytes(pdf)
    return path.read_bytes(), name


_warm_tasks: dict = {}


def warm_pdf(did, delay=90):
    """Jadval o'zgarganda PDF ni oldindan tayyorlab qo'yadi (tugma bosilganda darrov ochilsin)."""
    t = _warm_tasks.get(did)
    if t and not t.done():
        t.cancel()

    async def later():
        try:
            await asyncio.sleep(delay)
            await cached_pdf(did)
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa
            log.warning("PDF oldindan tayyorlanmadi: %s", e)
    _warm_tasks[did] = asyncio.get_event_loop().create_task(later())


async def pdf_public(request):
    """Guruhdagi jadval tagidagi «📄 PDF katalog» tugmasi — har doim jadvalning eng so'nggi holati."""
    did = request.match_info["did"].removesuffix(".pdf")
    d = store.designs.get(did)
    if not d or d.get("deleted"):
        raise web.HTTPNotFound(text="Topilmadi")
    pdf, name = await cached_pdf(did)
    return web.Response(body=pdf, content_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="{name}"', "Cache-Control": "no-cache"})


def routes(app: web.Application):
    app.router.add_get("/app", page)
    app.router.add_get("/app/", page)
    app.router.add_get("/api/catalog", catalog)
    app.router.add_get("/api/me", me)
    app.router.add_post("/api/admin", admin_action)
    app.router.add_post("/api/admin/photo", admin_photo)
    app.router.add_post("/api/pdf", pdf_export)
    app.router.add_post("/api/photos", photos_export)
    app.router.add_get("/pdf/{did}", pdf_public)
    app.router.add_get("/p/{did}/{iid}", product_page)
    app.router.add_get("/api/admin/info", admin_info)
    app.router.add_get("/img/{fid}", img)
    app.router.add_get("/static/logo.png", lambda r: web.FileResponse(
        config.ASSETS / "logo.png", headers={"Cache-Control": "public, max-age=86400"}))
