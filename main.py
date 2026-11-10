"""LENON katalog boti — asosiy dastur.

Ishga tushirish:  python main.py
Kerakli sozlamalar (Environment): BOT_TOKEN, OWNER_ID (va serverda PUBLIC_URL — Render o'zi beradi)."""
import os as _os
import sys as _sys

# BOT_TOKEN_UZ berilgan bo'lsa — bitta bepul xizmatda ikki bot ishlaydi (LENON 55 + LENON_UZ), qarang: run.py
if __name__ == "__main__" and _os.getenv("BOT_TOKEN_UZ") and not _os.getenv("LENON_CHILD"):
    import run
    run.main()
    _sys.exit(0)

import asyncio
import hashlib
import json
import logging
import os
import signal
from datetime import datetime

from aiohttp import web, ClientSession, ClientTimeout

import admin
import checker
import config
import notify
import publisher
import report
import webapi
import zaxira
from store import store
from tg import bot, TelegramError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")

OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
WEBHOOK_SECRET = hashlib.sha256(("lenon:" + config.BOT_TOKEN).encode()).hexdigest()[:32]


# ---------------- zaxira nusxa (egasining chatida, mahkamlangan fayl) ----------------
async def backup_to_telegram(data: bytes):
    """Zaxira faylini egasining chatidagi BITTA mahkamlangan xabarda yangilab boradi.
    Har safar yangi xabar yuborib, qayta mahkamlamaydi — shuning uchun «xabarni qadadi» degan yozuvlar chiqmaydi."""
    owner = store.settings.get("owner_id")
    if not owner:
        return
    stamp = datetime.now(config.TZ).strftime("%d.%m.%Y %H:%M")
    caption = f"💾 Katalog zaxirasi — {stamp}\n(o'chirmang, bot shundan tiklanadi)"
    old = store.settings.get("backup_msg_id")
    if old:
        try:
            await bot.call("editMessageMedia", files={"doc": ("lenon_baza.json", data, "application/json")},
                           chat_id=str(owner), message_id=str(old),
                           media={"type": "document", "media": "attach://doc", "caption": caption})
            return
        except TelegramError as e:
            if "not modified" in e.description:
                return
            log.info("Zaxira xabarini yangilab bo'lmadi, yangisi yuboriladi: %s", e)
    m = await bot.call("sendDocument", files={"document": ("lenon_baza.json", data, "application/json")},
                       chat_id=str(owner), caption=caption, disable_notification="true")
    try:
        await bot.call("pinChatMessage", chat_id=owner, message_id=m["message_id"], disable_notification=True)
    except TelegramError:
        pass
    if old and old != m["message_id"]:
        await bot.safe_delete(owner, old)
    store.settings["backup_msg_id"] = m["message_id"]
    store.save(backup=False)


async def restore_from_telegram():
    """Natija: True — tiklandi; False — zaxira umuman yo'q; None — zaxira bor-yo'qligini aniqlab bo'lmadi (xato)."""
    owner = store.settings.get("owner_id") or OWNER_ID
    if not owner:
        return False
    last = None
    for attempt in range(6):          # server endi uyg'onganda Telegram bilan aloqa birdan ishlamasligi mumkin
        try:
            chat = await bot.call("getChat", chat_id=owner)
            pm = chat.get("pinned_message") or {}
            doc = pm.get("document")
            if not doc or not doc.get("file_name", "").endswith(".json"):
                return False
            data = await bot.download(doc["file_id"])
            if store.load_bytes(data):
                log.info("Katalog Telegram'dagi zaxiradan tiklandi")
                return True
            last = "zaxira fayli o'qilmadi"
        except Exception as e:  # noqa
            last = str(e)
        log.warning("Zaxiradan tiklash urinish %d: %s", attempt + 1, last)
        await asyncio.sleep(5 + attempt * 5)
    return None


# ---------------- fon ishlari ----------------
async def background():
    last_ping = 0
    while True:
        try:
            await asyncio.sleep(60)
            # 0) avto-aylantirish (LENON_UZ): har soat boshida navbatdagi tovar
            if config.ROTATION:
                import rotation
                await rotation.tick()
            # 1) muddati o'tgan NEW/KELDI belgilarini olib tashlash
            for did in store.expire_marks():
                publisher.queue_update(did, delay=5)
            # 2) kunlik hisobot (8-agent)
            nowt = datetime.now(config.TZ)
            today = nowt.strftime("%Y-%m-%d")
            owner = store.settings.get("owner_id")
            if owner and nowt.hour >= config.REPORT_HOUR and store.settings.get("last_report") != today:
                store.settings["last_report"] = today
                store.save()
                try:
                    await bot.send(owner, await report.build(24))
                except Exception as e:  # noqa
                    await checker.report_error("Kunlik hisobot yuborilmadi", e)
            # 3) BotFather sozlamalari o'zgargan bo'lsa (masalan Mini App yoqilgan) — yangilab olamiz
            if int(asyncio.get_event_loop().time()) % 1800 < 60:
                try:
                    bot.me = await bot.call("getMe")
                except TelegramError:
                    pass
            # 4) server uxlab qolmasligi uchun o'zini "uyg'otib" turadi
            if config.PUBLIC_URL and asyncio.get_event_loop().time() - last_ping > 600:
                last_ping = asyncio.get_event_loop().time()
                try:
                    async with ClientSession(timeout=ClientTimeout(total=20)) as s:
                        await s.get(config.PUBLIC_URL + "/health")
                except Exception:  # noqa
                    pass
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa
            await checker.report_error("Fon ishlarida xato", e)


async def polling():
    """Server manzili bo'lmasa (masalan, kompyuterda sinash) — oddiy so'rov usuli."""
    await bot.call("deleteWebhook", drop_pending_updates=False)
    offset = 0
    while True:
        try:
            ups = await bot.call("getUpdates", offset=offset, timeout=50,
                                 allowed_updates=["message", "callback_query", "my_chat_member"])
            for u in ups:
                offset = u["update_id"] + 1
                asyncio.get_event_loop().create_task(admin.handle_update(u))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa
            log.error("polling: %s", e)
            await asyncio.sleep(5)


# ---------------- veb server ----------------
async def webhook(request):
    if request.match_info.get("secret") != WEBHOOK_SECRET:
        raise web.HTTPForbidden()
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        raise web.HTTPForbidden()
    u = await request.json()
    asyncio.get_event_loop().create_task(admin.handle_update(u))
    return web.Response(text="ok")


async def health(request):
    return web.json_response({"ok": True, "bot": bot.username, "designs": len(store.designs),
                              "photos": zaxira.photo_count(store.s), "auto": store.settings.get("zx_auto"),
                              "zx_scan": store.settings.get("zx_scan2"), "gr": store.settings.get("gr_photos2"), "chat": store.settings.get("chat_photos1"), "ibox": bool(os.getenv("IBOX_TOKEN") or (os.getenv("IBOX_LOGIN") and os.getenv("IBOX_PASSWORD"))), "group": bool(store.settings.get("group_id")),
                              "topics": sum(1 for v in (store.settings.get("topics") or {}).values() if v not in (None, -1)),
                              "commit": (os.getenv("RENDER_GIT_COMMIT") or "")[:7]})


async def root(request):
    raise web.HTTPFound("/app")


async def on_startup(app):
    if not config.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN berilmagan! Server sozlamalariga BOT_TOKEN qo'shing.")
    await bot.start()
    log.info("Bot: @%s", bot.username)

    # agentlarni bog'lash
    store.backup_hook = backup_to_telegram
    zaxira.hook = backup_to_telegram
    bot.sent_hook = publisher.record_sent
    publisher.error_hook = checker.report_error
    notify.error_hook = checker.report_error

    # ma'lumotni yuklash: kompyuter/server diskidan -> Telegram zaxirasidan -> boshlang'ich jadvallardan
    if not store.load_local():
        if OWNER_ID:
            store.settings["owner_id"] = OWNER_ID
        res = await restore_from_telegram()
        # GitHub'dagi zaxira Telegram'dagisidan yangiroq bo'lsa — o'shani olamiz
        import gitzaxira
        gst = await gitzaxira.fetch()
        if gst and (not res or float(gst.get("saved_at") or 0) > float(store.s.get("saved_at") or 0)):
            store.load_bytes(json.dumps(gst, ensure_ascii=False).encode("utf-8"))
            log.info("Katalog GitHub zaxirasidan tiklandi")
            res = True
        if not res:
            store.import_initial()
            store.settings["fresh_import"] = True
            log.info("Boshlang'ich katalog yuklandi: %d ta dizayn/nabor", len(store.designs))
        if res is None:
            # Zaxira bor bo'lishi mumkin, lekin o'qib bo'lmadi — uni boshlang'ich katalog bilan USTIDAN YOZMAYMIZ.
            store.backup_hook = None
            try:
                await bot.send(OWNER_ID, "⚠️ Server qayta ishga tushdi, lekin katalog zaxirasini o'qib bo'lmadi. "
                                         "Ma'lumot yo'qolmasligi uchun avtomatik zaxiralash to'xtatildi.\n\n"
                                         "Shu chatdagi oxirgi «💾 Katalog zaxirasi» faylini menga qayta yuboring "
                                         "(forward qiling) — katalogni o'shandan tiklayman.")
            except Exception:  # noqa
                pass
    # guruh unutilib qolgan bo'lsa (masalan, baza boshidan yuklangan) — ochiq guruh nomidan topamiz
    if not store.settings.get("group_id") and config.DEFAULT_GROUP_USERNAME:
        try:
            g = await bot.call("getChat", chat_id="@" + config.DEFAULT_GROUP_USERNAME)
            store.settings["group_id"] = g["id"]
            store.settings["group_username"] = config.DEFAULT_GROUP_USERNAME
            log.info("Guruh qayta topildi: %s", g["id"])
        except Exception as e:  # noqa
            log.warning("guruhni topib bo'lmadi: %s", e)
    if OWNER_ID and store.settings.get("owner_id") != OWNER_ID:
        store.settings["owner_id"] = OWNER_ID
    store.save(backup=False)

    # webhook
    if config.PUBLIC_URL:
        await bot.call("setWebhook", url=f"{config.PUBLIC_URL}/tg/{WEBHOOK_SECRET}",
                       secret_token=WEBHOOK_SECRET,
                       allowed_updates=["message", "callback_query", "my_chat_member"])
        log.info("Webhook o'rnatildi")
    else:
        app["polling"] = asyncio.get_event_loop().create_task(polling())

    # pastki «📒 Katalog» tugmasi va buyruqlar (maxfiy rejimda faqat adminlarga)
    await admin.sync_menu_buttons()
    if store.settings.get("privacy_applied") != store.settings.get("private", True):
        asyncio.get_event_loop().create_task(admin.apply_privacy())

    app["bg"] = asyncio.get_event_loop().create_task(background())
    # bir martalik: yo'qolgan ma'lumotni qaytarish uchun chatdagi eski zaxiralarni egasiga ko'rsatish
    if OWNER_ID and store.settings.get("zx_scan2") != 1:
        async def _zx_scan():
            await asyncio.sleep(20)
            try:
                await zaxira.offer_found(OWNER_ID)
                store.settings["zx_scan2"] = 1
                store.save()
            except Exception as e:  # noqa
                log.warning("zaxiralarni ko'rish: %s", e)
        asyncio.get_event_loop().create_task(_zx_scan())
    # bir martalik: guruhdagi postlardan katalog tugmalari va yo'riqnomani olib tashlash
    if store.settings.get("strip_btn2") != 1 and store.settings.get("group_id") and not config.ROTATION:
        async def _strip():
            await asyncio.sleep(30)
            try:
                if store.settings.get("strip_btn1") != 1:
                    await publisher.strip_catalog_buttons()
                    store.settings["strip_btn1"] = 1
                    store.save()
                n = await publisher.strip_old_buttons()
                log.info("eski xabarlardan tugmalar olindi: %s", n)
                store.settings["strip_btn2"] = 1
                store.save()
            except Exception as e:  # noqa
                log.warning("tugmalarni olib tashlash: %s", e)
        asyncio.get_event_loop().create_task(_strip())
    # bir martalik: yo'qolgan rasmlarni egasining bot chatidagi eski rasmlardan qaytarish (LENON 55)
    if OWNER_ID and not config.ROTATION and store.settings.get("chat_photos1") != 1:
        async def _chat_photos():
            await asyncio.sleep(40)
            try:
                import chatdan
                r = await chatdan.run(OWNER_ID)
                if r is not None:
                    store.settings["chat_photos1"] = 1
                    store.save()
            except Exception as e:  # noqa
                log.warning("chatdan rasmlar: %s", e)
        asyncio.get_event_loop().create_task(_chat_photos())
    # bir martalik: yo'qolgan dizayn rasmlarini guruhdagi postlardan qaytarish (LENON 55)
    if OWNER_ID and not config.ROTATION and store.settings.get("gr_photos2") != 1:
        async def _gr_photos():
            await asyncio.sleep(90)
            try:
                import guruhdan
                await guruhdan.run(OWNER_ID)
                store.settings["gr_photos2"] = 1
                store.save()
            except Exception as e:  # noqa
                log.warning("guruhdan rasmlar: %s", e)
        asyncio.get_event_loop().create_task(_gr_photos())
    if config.LENONUZ:
        import naborlar
        try:
            r = naborlar.migrate()
            if r and r[1] and store.settings.get("owner_id"):
                await bot.send(store.settings["owner_id"],
                               f"🗂 «Naborlar» bo'limi {len(r[0])} ta bo'limga bo'lindi: {', '.join(r[0])} "
                               f"({r[1]} ta nabor).\nEndi nabor butunligicha «qolmadi» qilinadi — ichidagi "
                               "buyumlarga alohida belgi qo'yilmaydi."
                               + (f"\nQolmadi deb belgilangan naborlar: {', '.join(r[2])}" if r[2] else ""))
        except Exception as e:  # noqa
            log.warning("naborlarni bo'lish: %s", e)
        import restore
        try:
            res = restore.apply_fix_file()
            if res and res[0] and store.settings.get("owner_id"):
                await bot.send(store.settings["owner_id"], "🛠 Qisqarib qolgan jadvallar asl postlardan to'ldirildi:\n"
                               + "\n".join("• " + x for x in res[0]) + "\nQolmadi belgilari saqlandi.")
        except Exception as e:  # noqa
            log.warning("jadval tuzatish: %s", e)
        import rotation
        asyncio.get_event_loop().create_task(rotation.pin_once())
    if config.LENONUZ:
        import ibox
        asyncio.get_event_loop().create_task(ibox.loop())
    if config.ROTATION:
        import dedup
        asyncio.get_event_loop().create_task(dedup.auto_clean_once())
    # har ishga tushganda: jadvallar tagidagi PDF lar to'liqmi — yetishmaganini joylaydi
    async def _ensure_pdfs_later():
        await asyncio.sleep(120)
        try:
            await publisher.ensure_pdfs(store.settings.get("owner_id"))
        except Exception as e:  # noqa
            await checker.report_error("PDF larni tekshirishda xato", e)
    if store.settings.get("group_id") and store.settings.get("pdf_msgs_v") == 1 and not config.ROTATION:
        asyncio.get_event_loop().create_task(_ensure_pdfs_later())

    # bir marta: hamma jadvallarni qayta joylash — har bir jadval tagida o'zining PDF katalogi bilan
    if (store.settings.get("pdf_msgs_v") != 1 and store.settings.get("group_id") and store.settings.get("owner_id")
            and not config.ROTATION):
        store.settings["pdf_msgs_v"] = 1
        store.save()

        async def _repost_with_pdfs():
            await asyncio.sleep(30)
            try:
                await admin.publish_all(store.settings["owner_id"], repost=True)
            except Exception as e:  # noqa
                await checker.report_error("Jadvallarni PDF bilan qayta joylashda xato", e)
        asyncio.get_event_loop().create_task(_repost_with_pdfs())
    # yozuvdan narx olib tashlangach — mahsulot rasmlarini bir marta avtomatik qayta tayyorlash
    owner_id = store.settings.get("owner_id")
    if owner_id and store.settings.get("relabel_done") != 2 and not config.ROTATION:

        async def _relabel_later():
            await asyncio.sleep(900)   # og'ir ishlar bir vaqtga to'g'ri kelmasin
            import relabel
            try:
                await relabel.run(owner_id)
            except Exception as e:  # noqa
                await checker.report_error("Rasmlarni qayta tayyorlashda xato", e)
        asyncio.get_event_loop().create_task(_relabel_later())
    owner = store.settings.get("owner_id")
    if owner:
        try:
            await bot.send(owner, "🟢 Bot ishga tushdi.", disable_notification=True)
        except TelegramError:
            pass


async def on_shutdown(app):
    for k in ("bg", "polling"):
        t = app.get(k)
        if t:
            t.cancel()
    try:
        await notify.flush()
    except Exception:  # noqa
        pass
    store.save(backup=False)
    await store.backup_now()
    await bot.close()


def make_app() -> web.Application:
    app = web.Application(client_max_size=10 * 1024 * 1024)
    app.router.add_get("/", root)
    app.router.add_get("/health", health)
    import gitzaxira
    app.router.add_get("/zaxira.enc", gitzaxira.handler)
    import eksport
    app.router.add_get("/eksport/rasmlar", eksport.handler)
    import guruhdan
    app.router.add_get("/diag/fwd", guruhdan.diag)
    app.router.add_post("/tg/{secret}", webhook)
    webapi.routes(app)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    return app


if __name__ == "__main__":
    web.run_app(make_app(), host="0.0.0.0", port=config.PORT)
