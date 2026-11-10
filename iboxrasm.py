"""LENON 55: botga qo'yilgan mahsulot rasmi iBox'dagi o'sha mahsulotga ham o'zi yuklanadi.

Ishlash tartibi (kompyuter kerak emas — server o'zi qiladi):
  1) Mahsulotga rasm qo'yiladi (bot yoki mini app orqali) → navbatga tushadi.
  2) iBox'da mahsulot topiladi: nom/SKU oxiridagi KOD + dizayn belgisi (masalan «010-IL-YA4978»,
     «EL-YA4978», «46-nabor … P823»).
  3) Rasm iBox'ga yuklanadi (POST /api/core/photo) va mahsulotning ASOSIY rasmi qilib saqlanadi
     (PUT /api/product/product/{id}; boshqa maydonlar iBox'dagi holicha qayta yuboriladi — iBox ilovasi
     o'zi «Saqlash»da yuboradigan ko'rinishda).
  4) Egasiga qisqa hisobot boradi.

Sozlash: Render → Environment → IBOX_TOKEN (iBox API kaliti). IBOX_URL, IBOX_FILIAL — ixtiyoriy."""
import asyncio
import html
import json
import logging
import os
import re

from aiohttp import ClientSession, ClientTimeout, FormData

import config
from store import store
from tg import bot

log = logging.getLogger("iboxrasm")
TOKEN = os.getenv("IBOX_TOKEN", "").strip()
LOGIN = os.getenv("IBOX_LOGIN", "").strip()          # API kalit bo'lmasa — iBox foydalanuvchisi bilan kirish
PASSWORD = os.getenv("IBOX_PASSWORD", "")
_sess_token = {"t": ""}
BASE = os.getenv("IBOX_URL", "https://torix.ibox.io").strip().rstrip("/")
FILIAL = os.getenv("IBOX_FILIAL", "1").strip() or "1"
_queue: list = []        # (design_id, item_id)
_task = None
_report: list = []


def enabled() -> bool:
    return bool(TOKEN or (LOGIN and PASSWORD)) and not config.ROTATION


def _find_token(j):
    """Kirish javobidan tokenni topadi (maydon nomi qanday bo'lishidan qat'i nazar)."""
    if isinstance(j, dict):
        for k in ("access_token", "token", "api_token", "plainTextToken"):
            v = j.get(k)
            if isinstance(v, str) and len(v) > 20:
                return v
        for v in j.values():
            t = _find_token(v)
            if t:
                return t
    return ""


async def _login(s):
    if TOKEN:
        _sess_token["t"] = TOKEN
        return
    last = ""
    for field in ("login", "username", "email", "phone"):
        async with s.post(BASE + "/api/user/login", json={field: LOGIN, "password": PASSWORD},
                          headers={"Accept": "application/json"}) as r:
            txt = await r.text()
            if r.status < 400:
                try:
                    t = _find_token(json.loads(txt))
                except Exception:  # noqa
                    t = ""
                if t:
                    _sess_token["t"] = t
                    return
            last = f"{r.status} {txt[:150]}"
    raise RuntimeError(f"iBox'ga kirib bo'lmadi (login/parolni tekshiring): {last}")


async def _req(s, method, path, **kw):
    """iBox so'rovi: token yo'q yoki eskirgan bo'lsa — qayta kiradi."""
    if not _sess_token["t"]:
        await _login(s)
    for attempt in (1, 2):
        h = dict(kw.pop("headers", {}) or {})
        h.update(_h(isinstance(kw.get("data"), str)))
        r = await s.request(method, BASE + path, headers=h, **kw)
        if r.status == 401 and attempt == 1 and not TOKEN:
            r.release()
            _sess_token["t"] = ""
            await _login(s)
            continue
        return r


def _h(json_body=False):
    h = {"Authorization": f"Bearer {_sess_token['t'] or TOKEN}", "Accept": "application/json", "Filial-Id": FILIAL}
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def _n(s):
    return re.sub(r"[^0-9a-z]+", "", str(s or "").lower())


def design_tag(d) -> tuple[str, str]:
    """(qidiruv belgisi, tur): «LENON 10-IL» -> ('010il', 'lenon'); «Lenon EL» -> ('el', 'el');
    nabor bo'limi -> ('46nabor', 'nabor')."""
    t = d["title"]
    sec = d.get("section", "")
    m = re.search(r"(\d{1,3})\s*-\s*([A-Za-z]{2})\b", t)
    if m and "lenon" in t.lower():
        return f"{int(m.group(1)):03d}{m.group(2).lower()}", "lenon"
    if re.search(r"\blenon\s*\.?\s*el\b", t, re.I):
        return "el", "el"
    m = re.search(r"nabor\s*(\d{2})", sec + " " + t, re.I)
    if m:
        return f"{m.group(1)}nabor", "nabor"
    return "", ""


def item_code(d, it) -> str:
    c = (it.get("code") or "").strip()
    if not c:
        m = re.search(r"\b([A-Z]\d{3,4}[\w-]*)", d["title"])   # naborlarda kod sarlavhada (P823)
        c = m.group(1) if m else ""
    return c


def _match(p, tag, kind, code) -> bool:
    name, sku = _n(p.get("name")), _n(p.get("sku"))
    cat = _n(p.get("product_category.name") or "")
    code = _n(code)
    if not code or not (name.endswith(code) or sku.endswith(code)):
        return False
    if kind == "lenon":
        return (tag in name or tag in sku) or cat == _n("lenon-" + tag[:3])
    if kind == "el":
        return ("el" + code) in name or ("el" + code) in sku
    if kind == "nabor":
        return tag in cat or tag in name
    return False


async def find_product(s, d, it):
    tag, kind = design_tag(d)
    code = item_code(d, it)
    if not tag or not code:
        return None, f"kod yoki dizayn belgisi aniqlanmadi ({d['title']})"
    params = [("search", code), ("search_by[]", "name"), ("search_by[]", "sku"),
              ("filters", '{"active":[true]}'), ("page", "1"), ("per_page", "100")]
    async with await _req(s, "GET", "/api/product/product", params=params) as r:
        if r.status == 401:
            raise RuntimeError("iBox'ga kirish rad etildi (401) — login/parol yoki kalitni tekshiring")
        j = await r.json(content_type=None)
    found = [p for p in (j.get("data") or []) if _match(p, tag, kind, code)]
    if not found:
        return None, f"iBox'da topilmadi: {code} ({d['title']})"
    if len(found) > 1:
        found.sort(key=lambda p: -float(p.get("stock") or 0))     # takror bo'lsa — qoldig'i ko'pi
    return found[0], ""


def _body(j, photo_id):
    """iBox ilovasi «Saqlash»da yuboradigan ko'rinish (tekshirilgan: maydonlar bir xil)."""
    return {
        "name": j["name"], "batch_tracking": None, "net_weight": j.get("net_weight"),
        "gross_weight": j.get("gross_weight"), "volume": j.get("volume"), "vat_percent": j.get("vat_percent"),
        "cashback_percentage": j.get("cashback_percentage"),
        "minimum_sales_quantity": j.get("minimum_sales_quantity"), "photo_id": photo_id, "sku": j.get("sku"),
        "comment": j.get("comment"),
        "price_types": [{"price": p.get("price"), "price_type_id": p.get("id"),
                         "currency_id": (p.get("currency") or {}).get("id")} for p in (j.get("price_types") or [])],
        "order_lead_time": j.get("order_lead_time"), "order": j.get("order"), "is_sellable": j.get("is_sellable"),
        "non_returnable": j.get("non_returnable"), "is_assembly_product": j.get("is_assembly_product"),
        "is_raw_material": j.get("is_raw_material"), "is_trackable": j.get("is_trackable"),
        "is_weighed_product": j.get("is_weighed_product"), "tasnif_ikpu": j.get("tasnif_ikpu"),
        "tasnif_unit_code": j.get("tasnif_unit_code"), "tasnif_barcode": j.get("tasnif_barcode"),
        "tasnif_label": j.get("tasnif_label"), "opening_balances": [],
        "storage_unit_id": j.get("storage_unit_id"), "extra_unit_id": j.get("extra_unit_id"),
        "default_unit_id": j.get("default_unit_id"), "gross_weight_unit_id": j.get("gross_weight_unit_id"),
        "volume_unit_id": j.get("volume_unit_id"), "net_weight_unit_id": j.get("net_weight_unit_id"),
        "product_category_id": j.get("product_category_id"), "product_group_id": j.get("product_group_id"),
        "product_brand_id": j.get("product_brand_id"), "main_supplier_id": j.get("main_supplier_id"),
        "owner_id": j.get("owner_id"),
        "pack_quantities": [{"unit_id": q.get("unit_id"), "parent_unit_id": j.get("storage_unit_id"),
                             "coefficient": q.get("coefficient"), "bar_code": q.get("bar_code"),
                             "bar_code_type": q.get("bar_code_type")} for q in (j.get("pack_quantities") or [])],
        "bar_code_type": None, "minimal_stocks": j.get("minimal_stocks"),
        "product_photo_ids": [p["id"] for p in (j.get("photos") or [])],
        "barcodes": [{"id": b.get("id"), "barcode": b.get("barcode"), "type": b.get("type"),
                      "product_id": b.get("product_id")} for b in (j.get("barcodes") or [])],
    }


async def upload_one(s, d, it, only_missing=False) -> str:
    p, err = await find_product(s, d, it)
    if not p:
        return "❓ " + err
    if only_missing and p.get("photo_id"):
        return ""
    data = await bot.download(it["photo"])
    form = FormData()
    form.add_field("photo", data, filename=f"{item_code(d, it) or 'rasm'}.jpg", content_type="image/jpeg")
    async with await _req(s, "POST", "/api/core/photo", data=form) as r:
        if r.status >= 400:
            return f"⚠️ {it['name']}: rasm yuklanmadi ({r.status})"
        ph = await r.json(content_type=None)
    async with await _req(s, "GET", f"/api/product/product/{p['id']}") as r:
        j = await r.json(content_type=None)
    async with await _req(s, "PUT", f"/api/product/product/{p['id']}", data=json.dumps(_body(j, ph["id"]))) as r:
        if r.status >= 400:
            return f"⚠️ {it['name']}: saqlanmadi ({r.status} {(await r.text())[:120]})"
    return f"✅ {j['name'].split('  ')[0].strip()}"


def add(design_id, item_id):
    """Rasm qo'yilganda chaqiriladi."""
    global _task
    if not enabled():
        return
    _queue.append((design_id, item_id))
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return
    if _task is None or _task.done():
        _task = loop.create_task(_worker())


async def _worker():
    await asyncio.sleep(5)
    lines = []
    async with ClientSession(timeout=ClientTimeout(total=120)) as s:
        while _queue:
            did, iid = _queue.pop(0)
            d = store.designs.get(did)
            if not d:
                continue
            _, it = store.find_item(d, iid)
            if not it or not it.get("photo"):
                continue
            try:
                res = await upload_one(s, d, it)
            except Exception as e:  # noqa
                res = f"⚠️ {it['name']}: {e}"
            lines.append(res)
            await asyncio.sleep(1)
            if not _queue:
                await asyncio.sleep(6)       # ketma-ket rasm qo'yilsa — bitta hisobot
    owner = store.settings.get("owner_id")
    if owner and lines:
        await bot.send(owner, "🖼 <b>iBox'ga rasm</b>\n" + "\n".join(html.escape(x) for x in lines[:60]),
                       disable_notification=True)


async def push_design(uid, query: str, only_missing=True):
    """/iboxga 07-DL — dizayndagi barcha rasmli mahsulotlarni iBox'ga yuklash."""
    if not enabled():
        await bot.send(uid, "iBox ulanmagan. Render → Environment ga <code>IBOX_LOGIN</code> va "
                            "<code>IBOX_PASSWORD</code> (yoki <code>IBOX_TOKEN</code>) qo'shing.")
        return
    q = _n(query)
    ds = [d for d in store.designs.values() if not d.get("deleted") and q and q in _n(d["title"])]
    if not ds:
        await bot.send(uid, "Dizayn topilmadi. Masalan: <code>/iboxga 07-DL</code>")
        return
    for d in ds:
        items = [it for c in d["categories"] for it in c["items"] if it.get("photo")]
        await bot.send(uid, f"⏳ {html.escape(d['title'])}: {len(items)} ta rasm iBox'ga yuklanmoqda…",
                       disable_notification=True)
        lines, ok = [], 0
        async with ClientSession(timeout=ClientTimeout(total=120)) as s:
            for it in items:
                try:
                    res = await upload_one(s, d, it, only_missing=only_missing)
                except Exception as e:  # noqa
                    res = f"⚠️ {it['name']}: {e}"
                if res.startswith("✅"):
                    ok += 1
                elif res:
                    lines.append(res)
                await asyncio.sleep(0.7)
        no = [it["name"] for c in d["categories"] for it in c["items"] if not it.get("photo")]
        msg = f"✅ <b>{html.escape(d['title'])}</b>: {ok} ta rasm iBox'ga yuklandi."
        if lines:
            msg += "\n" + "\n".join(html.escape(x) for x in lines[:40])
        if no:
            msg += "\n\nBotda rasmi yo'q: " + html.escape(", ".join(no[:40]))
        await bot.send(uid, msg[:4000])
