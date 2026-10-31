"""LENON_UZ: eski (Cloudflare) botdagi to'liq jadvallarni tiklash.

Ko'chirishda ba'zi tovarlarning jadvali qisqarib qolgan (oxirgi qatorlari tushib qolgan,
sarlavha qatori «Nomi | Narxi» esa mahsulot bo'lib qolgan). /tiklash buyrug'i eski botning
/api/catalog manzilidan to'liq ma'lumotni oladi, nimalar qo'shilishini ko'rsatadi va
egasi «✅ Tasdiqlash» ni bosgandagina katalogni yangilaydi."""
import logging
import os
import re

from aiohttp import ClientSession, ClientTimeout

import publisher
from store import store

log = logging.getLogger("restore")
OLD_URL = os.getenv("OLD_BOT_URL", "https://lenon-bot.lenonuzb.workers.dev").rstrip("/")
_plan = {}   # uid -> [(design_id, new_categories)]

HEAD_WORDS = ("nomi", "kodi", "soni", "narxi", "jami", "no.", "№")


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def _is_header(cells) -> bool:
    words = [_norm(c) for c in cells if _norm(c)]
    return bool(words) and all(any(w.startswith(h) for h in HEAD_WORDS) for w in words)


def _colmap(cells) -> dict:
    m = {}
    for i, c in enumerate(cells):
        w = _norm(c)
        for key, pref in (("name", "nom"), ("code", "kod"), ("qty", "son"), ("price", "narx"), ("total", "jami")):
            if w.startswith(pref) and key not in m:
                m[key] = i
    return m


def _guess(cells) -> dict:
    """Sarlavhasiz qator: nomi, (kodi), soni, narxi, (jami) ni ko'rinishidan topadi."""
    cells = [str(c or "").strip() for c in cells]
    out = {"name": cells[0] if cells else "", "code": "", "qty": "", "price": "", "total": ""}
    money = [i for i, c in enumerate(cells[1:], 1) if re.search(r"\$|so'?m|сум", c, re.I)]
    qty = [i for i, c in enumerate(cells[1:], 1) if re.search(r"шт|dona|ta\b|pcs", c, re.I) and i not in money]
    if money:
        out["price"] = cells[money[0]]
        if len(money) > 1:
            out["total"] = cells[money[-1]]
    if qty:
        out["qty"] = cells[qty[0]]
    rest = [c for i, c in enumerate(cells[1:], 1) if i not in money and i not in qty and c]
    if rest:
        out["code"] = rest[0]
    return out


def _parse(p) -> list:
    cats, cur, cmap = [], None, None
    for r in p.get("rows") or []:
        cells = r.get("c") or []
        if r.get("t"):
            cur = {"name": str(cells[0] if cells else "").strip() or "Asosiy", "items": []}
            cats.append(cur)
            continue
        if r.get("h") or _is_header(cells):
            cmap = _colmap(cells)
            continue
        if not any(str(c or "").strip() for c in cells):
            continue
        if cmap and "name" in cmap:
            g = lambda k: str(cells[cmap[k]]).strip() if k in cmap and cmap[k] < len(cells) else ""  # noqa
            it = {k: g(k) for k in ("name", "code", "qty", "price", "total")}
        else:
            it = _guess(cells)
        if not it["name"]:
            continue
        it["in_stock"] = not r.get("x")
        if cur is None:
            cur = {"name": "Asosiy", "items": []}
            cats.append(cur)
        cur["items"].append(it)
    return [c for c in cats if c["items"]]


def _count(cats) -> int:
    return sum(len(c["items"]) for c in cats)


def _junk(it) -> bool:
    return _is_header([it.get("name"), it.get("code"), it.get("qty"), it.get("price")])


def _match(p, by_photo, by_title):
    for ph in p.get("photos") or []:
        fid = ph[0] if isinstance(ph, (list, tuple)) else ph
        if fid in by_photo:
            return by_photo[fid]
    return by_title.get(_norm(p.get("title")))


async def build(uid) -> str:
    async with ClientSession(timeout=ClientTimeout(total=40)) as s:
        async with s.get(OLD_URL + "/api/catalog") as r:
            if r.status != 200:
                return f"⚠️ Eski botdan ma'lumot olinmadi (javob {r.status})."
            data = await r.json(content_type=None)
    alive = [d for d in store.designs.values() if not d.get("deleted")]
    by_photo, by_title = {}, {}
    for d in alive:
        for f in d.get("photos") or []:
            by_photo.setdefault(f, d["id"])
        by_title.setdefault(_norm(d["title"]), d["id"])
    plan, lines, junk_only = [], [], 0
    for p in data.get("products") or []:
        did = _match(p, by_photo, by_title)
        if not did:
            continue
        d = store.designs[did]
        new = _parse(p)
        cur_n = sum(1 for c in d["categories"] for it in c["items"] if not _junk(it))
        has_junk = any(_junk(it) for c in d["categories"] for it in c["items"])
        if _count(new) > cur_n:
            plan.append((did, new))
            lines.append(f"• {publisher.esc(d['title'])}: {cur_n} → <b>{_count(new)}</b>")
        elif has_junk:
            plan.append((did, None))
            junk_only += 1
    _plan[uid] = plan
    if not plan:
        return ("✅ Tekshirildi: katalogdagi jadvallar eski botdagidek to'liq, qo'shiladigan narsa yo'q.\n"
                f"(Eski botda {len(data.get('products') or [])} ta tovar bor.)")
    head = f"🔎 Eski botdagi jadvallar bilan solishtirildi.\n\n<b>To'ldiriladigan tovarlar: {len(lines)} ta</b>\n"
    body = "\n".join(lines[:60]) + ("\n…" if len(lines) > 60 else "")
    tail = (f"\n\nYana {junk_only} ta tovardan ortiqcha «Nomi | Narxi» qatori olib tashlanadi." if junk_only else "")
    return head + body + tail + "\n\nQolmadi belgilari saqlanadi. Tasdiqlaysizmi?"


def apply(uid) -> int:
    plan = _plan.pop(uid, None) or []
    n = 0
    for did, new in plan:
        d = store.designs.get(did)
        if not d:
            continue
        old = {}
        for c in d["categories"]:
            for it in c["items"]:
                if not _junk(it):
                    old.setdefault(_norm(it["name"]), it)
        if new is None:
            for c in d["categories"]:
                c["items"] = [it for it in c["items"] if not _junk(it)]
            d["categories"] = [c for c in d["categories"] if c["items"]]
        else:
            cats = []
            for c in new:
                items = []
                for it in c["items"]:
                    prev = old.pop(_norm(it["name"]), None)
                    x = store._mk_item(it["name"], it["code"], it["price"], new=False, qty=it["qty"], total=it["total"])
                    if prev:  # mavjud mahsulot: id va qolmadi/keldi belgisi saqlanadi
                        for k in ("id", "in_stock", "new_until", "keldi_until", "photo"):
                            if k in prev:
                                x[k] = prev[k]
                    else:
                        x["in_stock"] = it["in_stock"]
                    items.append(x)
                cats.append({"name": c["name"], "items": items})
            d["categories"] = cats
        n += 1
    store.save()
    return n
