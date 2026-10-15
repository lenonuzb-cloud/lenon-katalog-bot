"""LENON katalog boti — asosiy dastur.

Ishga tushirish:  python main.py
Kerakli sozlamalar (Environment): BOT_TOKEN, OWNER_ID (va serverda PUBLIC_URL — Render o'zi beradi)."""
import asyncio
import hashlib
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
from store import store
from tg import bot, TelegramError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")

OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
WEBHOOK_SECRET = hashlib.sha256(("lenon:" + config.BOT_TOKEN).encode()).hexdigest()[:32]


# ---------------- zaxira nusxa (egasining chatida, mahkamlangan fayl) ----------------
async def backup_to_telegram(data: bytes):
    owner = store.settings.get("owner_id")
    if not owner:
        return
    stamp = datetime.now(config.TZ).strftime("%d.%m.%Y %H:%M")
    m = await bot.call("sendDocument", files={"document": ("lenon_baza.json", data, "application/json")},
                       chat_id=str(owner), caption=f"💾 Katalog zaxirasi — {stamp}\n(o'chirmang, bot shundan tiklanadi)",
                       disable_notification="true")
    try:
        await bot.call("pinChatMessage", chat_id=owner, message_id=m["message_id"], disable_notification=True)
    except TelegramError:
        pass
    old = store.settings.get("backup_msg_id")
    if old and old != m["message_id"]:
        await bot.safe_delete(owner, old)
    store.settings["backup_msg_id"] = m["message_id"]
    store.save(backup=False)


async def restore_from_telegram() -> bool:
    owner = store.settings.get("owner_id") or OWNER_ID
    if not owner:
        return False
    try:
        chat = await bot.call("getChat", chat_id=owner)
        pm = chat.get("pinned_message") or {}
        doc = pm.get("document")
        if not doc or not doc.get("file_name", "").endswith(".json"):
            return False
        data = await bot.download(doc["file_id"])
        ok = store.load_bytes(data)
        if ok:
            log.info("Katalog Telegram'dagi zaxiradan tiklandi")
        return ok
    except TelegramError as e:
        log.warning("Zaxiradan tiklab bo'lmadi: %s", e)
        return False


# ---------------- fon ishlari ----------------
async def background():
    last_ping = 0
    while True:
        try:
            await asyncio.sleep(60)
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
    return web.json_response({"ok": True, "bot": bot.username, "designs": len(store.designs)})


async def root(request):
    raise web.HTTPFound("/app")


async def on_startup(app):
    if not config.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN berilmagan! Server sozlamalariga BOT_TOKEN qo'shing.")
    await bot.start()
    log.info("Bot: @%s", bot.username)

    # agentlarni bog'lash
    store.backup_hook = backup_to_telegram
    publisher.error_hook = checker.report_error
    notify.error_hook = checker.report_error

    # ma'lumotni yuklash: kompyuter/server diskidan -> Telegram zaxirasidan -> boshlang'ich jadvallardan
    if not store.load_local():
        if OWNER_ID:
            store.settings["owner_id"] = OWNER_ID
        if not await restore_from_telegram():
            store.import_initial()
            log.info("Boshlang'ich katalog yuklandi: %d ta dizayn/nabor", len(store.designs))
    if OWNER_ID and store.settings.get("owner_id") != OWNER_ID:
        store.settings["owner_id"] = OWNER_ID
    store.save(backup=False)

    # pastki "Katalog" tugmasi (shaxsiy chatlarda)
    if config.PUBLIC_URL:
        try:
            await bot.call("setChatMenuButton", menu_button={"type": "web_app", "text": "📒 Katalog",
                                                             "web_app": {"url": config.PUBLIC_URL + "/app"}})
        except TelegramError as e:
            log.warning("Menyu tugmasi qo'yilmadi: %s", e)
        await bot.call("setWebhook", url=f"{config.PUBLIC_URL}/tg/{WEBHOOK_SECRET}",
                       secret_token=WEBHOOK_SECRET,
                       allowed_updates=["message", "callback_query", "my_chat_member"])
        log.info("Webhook o'rnatildi")
    else:
        app["polling"] = asyncio.get_event_loop().create_task(polling())

    try:
        await bot.call("setMyCommands", commands=[
            {"command": "start", "description": "Asosiy menyu / katalog"},
            {"command": "hisobot", "description": "Hisobot"},
            {"command": "tekshir", "description": "Nazoratchi tekshiruvi"},
            {"command": "qolmadi", "description": "Qolmadi / Keldi belgilash"},
        ])
    except TelegramError:
        pass

    app["bg"] = asyncio.get_event_loop().create_task(background())
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
    app.router.add_post("/tg/{secret}", webhook)
    webapi.routes(app)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    return app


if __name__ == "__main__":
    web.run_app(make_app(), host="0.0.0.0", port=config.PORT)
