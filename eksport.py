"""Vaqtinchalik: dizayn mahsulot rasmlarini (yozuvli) kod bilan JSON qilib berish — iBox'ga yuklash uchun.
Kalit bilan himoyalangan (kodda faqat kalitning xeshi turadi)."""
import base64
import hashlib
import hmac
import re

from aiohttp import web

from store import store
from tg import bot

KEY_SHA = "d06f7ce239763569e0316ee184d55e430daccf93231de2eb41deb80d43ecb365"


def _ok(k: str) -> bool:
    return hmac.compare_digest(hashlib.sha256((k or "").encode()).hexdigest(), KEY_SHA)


def _n(s):
    return re.sub(r"[^0-9a-z]+", "", (s or "").lower())


async def handler(request):
    cors = {"Access-Control-Allow-Origin": "*"}
    if not _ok(request.query.get("k", "")):
        return web.json_response({"error": "forbidden"}, status=403, headers=cors)
    want = _n(request.query.get("d", ""))
    d = next((x for x in store.designs.values() if not x.get("deleted") and _n(x["title"]).endswith(want)), None)
    if not d:
        return web.json_response({"error": "design not found"}, status=404, headers=cors)
    out, miss = [], []
    for c in d["categories"]:
        for it in c["items"]:
            if not it.get("photo"):
                miss.append({"name": it["name"], "code": it.get("code", "")})
                continue
            if request.query.get("list"):
                out.append({"name": it["name"], "code": it.get("code", ""), "cat": c["name"]})
                continue
            try:
                data = await bot.download(it["photo"])
            except Exception:  # noqa
                miss.append({"name": it["name"], "code": it.get("code", ""), "err": 1})
                continue
            out.append({"name": it["name"], "code": it.get("code", ""), "cat": c["name"],
                        "b64": base64.b64encode(data).decode()})
    return web.json_response({"design": d["title"], "items": out, "missing": miss}, headers=cors)
