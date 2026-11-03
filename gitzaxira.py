"""Ikkinchi, doimiy zaxira: GitHub.

Server katalogni bot tokenidan yasalgan kalit bilan SHIFRLAB /zaxira.enc manzilida beradi
(kalitsiz hech kim o'qiy olmaydi). GitHub Actions har 10 daqiqada shu faylni olib, «zaxira»
tarmog'iga yozib boradi — har bir holat tarixda qoladi. Server qayta yonganda katalogni
Telegram'dagi zaxira va GitHub'dagi zaxiradan qaysi biri yangiroq bo'lsa, o'shandan tiklaydi."""
import hashlib
import hmac
import zlib
import json
import logging
import time

from aiohttp import web, ClientSession, ClientTimeout
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import config
from store import store

log = logging.getLogger("gitzaxira")
REPO_RAW = "https://raw.githubusercontent.com/lenonuzb-cloud/lenon-katalog-bot/zaxira/"
MAGIC = b"LZ2"


def _key() -> bytes:
    return hashlib.sha256(("lenon-zaxira:" + config.BOT_TOKEN).encode()).digest()


def encrypt(data: bytes) -> bytes:
    data = zlib.compress(data, 9)
    # bir xil ma'lumot -> bir xil fayl (o'zgarmasa GitHub'ga qayta yozilmaydi)
    nonce = hmac.new(_key(), data, hashlib.sha256).digest()[:12]
    return MAGIC + nonce + AESGCM(_key()).encrypt(nonce, data, MAGIC)


def decrypt(blob: bytes) -> bytes:
    if not blob.startswith(MAGIC):
        raise ValueError("zaxira formati noto'g'ri")
    return zlib.decompress(AESGCM(_key()).decrypt(blob[3:15], blob[15:], MAGIC))


def name() -> str:
    return f"{config.PROFILE}.enc"


async def fetch() -> dict | None:
    """GitHub'dagi eng oxirgi zaxira (ochilgan holatda) yoki None."""
    try:
        async with ClientSession(timeout=ClientTimeout(total=60)) as s:
            async with s.get(REPO_RAW + name() + f"?t={int(time.time())}") as r:
                if r.status != 200:
                    return None
                blob = await r.read()
        st = json.loads(decrypt(blob).decode("utf-8"))
        return st if "designs" in st else None
    except Exception as e:  # noqa
        log.warning("GitHub zaxirasi olinmadi: %s", e)
        return None


async def handler(request):
    # Boshidan yuklangan (zaxiradan tiklanmagan) holatni zaxiraga bermaymiz — yaxshi zaxira buzilmasin
    if store.settings.get("fresh_import"):
        return web.Response(status=503, text="fresh")
    return web.Response(body=encrypt(store.dump_bytes()), content_type="application/octet-stream")
