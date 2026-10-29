"""Bitta bepul Render xizmatida IKKI botni ishlatish.

Render'ning bepul rejasida oyiga 750 soat beriladi — ikki alohida xizmat 24/7 ishlasa yetmaydi.
Shuning uchun:
  • LENON 55 boti (@lenonkatalogbot)    — ichki port 8101, manzil:  https://…onrender.com/
  • LENON_UZ boti (@lenonyordamchibot)   — ichki port 8102, manzil:  https://…onrender.com/uz/
Bu fayl ikkalasini ishga tushiradi va tashqi so'rovlarni manzilga qarab tegishli botga uzatadi.
BOT_TOKEN_UZ berilmagan bo'lsa — faqat bitta (LENON 55) bot oddiy ishlaydi."""
import asyncio
import os
import signal
import subprocess
import sys

from aiohttp import web, ClientSession, ClientTimeout

PORT = int(os.getenv("PORT", "8080"))
BASE = (os.getenv("PUBLIC_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")
UZ_TOKEN = os.getenv("BOT_TOKEN_UZ", "").strip()
HERE = os.path.dirname(os.path.abspath(__file__))


CHILDREN = {
    "": {"port": 8101, "env": {"PROFILE": "lenon55", "PUBLIC_URL": BASE,
                               "CACHE_DIR": "/tmp/lenon_cache_55"}},
    "/uz": {"port": 8102, "env": {"PROFILE": "lenonuz", "PUBLIC_URL": BASE + "/uz", "BOT_TOKEN": UZ_TOKEN,
                                  "DATA_DIR": os.path.join(HERE, "data_uz"), "CACHE_DIR": "/tmp/lenon_cache_uz",
                                  "OWNER_ID": os.getenv("OWNER_ID_UZ", os.getenv("OWNER_ID", ""))}},
}
procs: dict = {}


def start(prefix):
    c = CHILDREN[prefix]
    env = dict(os.environ)
    env.pop("BOT_TOKEN_UZ", None)
    env.update(c["env"])
    env["PORT"] = str(c["port"])
    env["LENON_CHILD"] = "1"
    procs[prefix] = subprocess.Popen([sys.executable, os.path.join(HERE, "main.py")], env=env)
    print(f"[run] {'LENON_UZ' if prefix else 'LENON 55'} boti ishga tushdi (port {c['port']})", flush=True)


async def watchdog():
    """Bot to'xtab qolsa — qayta ishga tushiradi."""
    while True:
        await asyncio.sleep(5)
        for prefix, p in list(procs.items()):
            if p.poll() is not None:
                print(f"[run] {prefix or '/'} boti to'xtadi (kod {p.returncode}), qayta ishga tushirilmoqda", flush=True)
                await asyncio.sleep(3)
                start(prefix)


HOP = {"host", "content-length", "transfer-encoding", "connection", "keep-alive", "upgrade",
       "proxy-connection", "te", "trailer"}


async def proxy(request: web.Request):
    path = request.rel_url.raw_path
    prefix = "/uz" if path == "/uz" or path.startswith("/uz/") else ""
    target_path = path[len(prefix):] or "/"
    port = CHILDREN[prefix]["port"]
    url = f"http://127.0.0.1:{port}{target_path}"
    if request.rel_url.query_string:
        url += "?" + request.rel_url.query_string
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
    body = await request.read()
    try:
        async with request.app["session"].request(request.method, url, headers=headers, data=body or None,
                                                  allow_redirects=False,
                                                  skip_auto_headers=("Accept-Encoding", "Content-Type")) as r:
            data = await r.read()
            out = {k: v for k, v in r.headers.items() if k.lower() not in HOP}
            if prefix and "Location" in out and out["Location"].startswith("/"):
                out["Location"] = prefix + out["Location"]
            return web.Response(status=r.status, body=data, headers=out)
    except Exception as e:  # noqa
        return web.Response(status=503, text=f"bot hali ishga tushmoqda: {e}")


async def on_startup(app):
    app["session"] = ClientSession(timeout=ClientTimeout(total=300), auto_decompress=False)
    for prefix in CHILDREN:
        start(prefix)
    app["wd"] = asyncio.get_event_loop().create_task(watchdog())


async def on_cleanup(app):
    app["wd"].cancel()
    for p in procs.values():
        p.send_signal(signal.SIGTERM)
    for p in procs.values():
        try:
            p.wait(timeout=20)
        except Exception:  # noqa
            p.kill()
    await app["session"].close()


def main():
    app = web.Application(client_max_size=12 * 1024 * 1024)
    app.router.add_route("*", "/{tail:.*}", proxy)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
