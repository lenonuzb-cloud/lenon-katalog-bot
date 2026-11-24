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


async def narxlar(request):
    """Barcha dizaynlarning mahsulot narxlari (iBox bilan solishtirish uchun)."""
    cors = {"Access-Control-Allow-Origin": "*"}
    if not _ok(request.query.get("k", "")):
        return web.json_response({"error": "forbidden"}, status=403, headers=cors)
    out = []
    for d in store.designs.values():
        if d.get("deleted"):
            continue
        for c in d["categories"]:
            for it in c["items"]:
                out.append({"design": d["title"], "section": d.get("section"), "cat": c["name"],
                            "name": it["name"], "code": it.get("code", ""), "price": it.get("price", ""),
                            "did": d["id"], "iid": it["id"]})
    return web.json_response({"items": out}, headers=cors)



async def narx_yangila(request):
    """Narxlarni yangilash: [{did, iid, price}] — guruh postlari yangilanadi; BODEN o'zgarishlari
    Muhokama bo'limiga e'lon qilinadi."""
    import html as _h
    import ops
    import publisher
    cors = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Content-Type"}
    if request.method == "OPTIONS":
        return web.Response(headers=cors)
    if not _ok(request.query.get("k", "")):
        return web.json_response({"error": "forbidden"}, status=403, headers=cors)
    body = await request.json()
    done, bod, touched = 0, {}, set()
    for ch in body.get("changes", []):
        d = store.designs.get(ch.get("did"))
        if not d:
            continue
        _, it = store.find_item(d, ch.get("iid"))
        if not it:
            continue
        new = str(ch["price"]).strip()
        if not new.endswith("$"):
            new += "$"
        old = it.get("price", "")
        if old == new:
            continue
        ops.edit_item(d["id"], it["id"], price=new)
        touched.add(d["id"])
        done += 1
        if d.get("section") == "boden":
            bod.setdefault(d["title"], []).append(f"{it['name']}: {old} → {new}")
    for i, did in enumerate(sorted(touched)):
        publisher.queue_update(did, delay=5 + i * 4)
    posted = False
    if bod and body.get("announce", True):
        st = store.settings
        gid, thread = st.get("group_id"), (st.get("topics") or {}).get("muhokama")
        if gid and thread:
            lines = ["💲 <b>BODEN — narxlar yangilandi</b>", ""]
            for t in sorted(bod):
                lines.append(f"<b>{_h.escape(t)}</b>")
                lines += ["• " + _h.escape(x) for x in bod[t]]
                lines.append("")
            text = "\n".join(lines)
            for i in range(0, len(text), 3800):
                await bot.call("sendMessage", chat_id=gid, message_thread_id=thread, text=text[i:i + 3800],
                               parse_mode="HTML")
            posted = True
    owner = store.settings.get("owner_id")
    if owner and done:
        await bot.send(owner, f"💲 iBox bo'yicha {done} ta narx yangilandi ({len(touched)} ta dizayn)."
                              + (" BODEN o'zgarishlari Muhokamaga e'lon qilindi." if posted else ""))
    return web.json_response({"done": done, "designs": len(touched), "posted": posted,
                              "boden": sum(len(v) for v in bod.values())}, headers=cors)
