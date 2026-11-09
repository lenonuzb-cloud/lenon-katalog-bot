"""LENON_UZ ← iBox: qoldiq (qolmadi/keldi), narx va rasmlarni avtomatik olib kelish.

Faqat O'QIYDI (GET so'rovlar) — iBox'da hech narsa o'zgartirilmaydi.

Sozlash (Render → Environment):
  IBOX_TOKEN   — iBox'da yaratilgan access token (majburiy)
  IBOX_URL     — iBox manzili, masalan https://torix.ibox.io (bo'lmasa shu ishlatiladi)
  IBOX_FILIAL  — filial raqami (bo'lmasa 1)
Ombor va narx turini egasi botda /ibox orqali tanlaydi.

Bog'lash: katalogdagi KOD (donalik mahsulotda «Kodi», naborda «Kodi: P823») iBox'dagi SKU bilan
solishtiriladi — katta-kichik harf, probel va chiziqchaga qaralmaydi. Topilmasa — nom bo'yicha.
"""
import asyncio
import json
import logging
import os
import re
import time

from aiohttp import ClientSession, ClientTimeout

import config
from store import store

log = logging.getLogger("ibox")
TOKEN = os.getenv("IBOX_TOKEN", "").strip()
BASE = os.getenv("IBOX_URL", "https://torix.ibox.io").strip().rstrip("/")
FILIAL = os.getenv("IBOX_FILIAL", "1").strip() or "1"
EVERY = 15 * 60          # sekund: har 15 daqiqada
_lock = asyncio.Lock()


def enabled() -> bool:
    return bool(config.LENONUZ and TOKEN)


def st() -> dict:
    s = store.settings.setdefault("ibox", {})
    s.setdefault("links", {})       # katalog kaliti -> iBox product id (qo'lda bog'langanlar)
    s.setdefault("photos_done", {})  # design_id -> iBox rasm manzili (bir marta qo'yilgan)
    s.setdefault("prices", False)  # avval egasi ko'rib, keyin yoqadi (valyuta adashmasligi uchun)
    s.setdefault("stock", True)
    s.setdefault("images", True)
    return s


def norm(code) -> str:
    return re.sub(r"[^0-9a-zа-яё]", "", str(code or "").lower())


# ---------------- HTTP ----------------
async def _get(session, path, **params):
    headers = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json", "Filial-Id": str(FILIAL)}
    q = []
    for k, v in params.items():
        if v is None or v == "":
            continue
        if isinstance(v, (list, tuple)):
            q += [(f"{k}[]", str(x)) for x in v]
        elif isinstance(v, bool):
            q.append((k, "1" if v else "0"))
        else:
            q.append((k, str(v)))
    async with session.get(BASE + path, params=q, headers=headers) as r:
        text = await r.text()
        if r.status == 401:
            raise RuntimeError("iBox token noto'g'ri yoki muddati o'tgan (401)")
        if r.status == 403:
            raise RuntimeError("iBox: bu tokenga ruxsat yo'q (403) — tokenni yaratgan foydalanuvchi huquqlarini tekshiring")
        if r.status >= 400:
            raise RuntimeError(f"iBox {path}: {r.status} {text[:200]}")
        try:
            return json.loads(text)
        except Exception:  # noqa
            raise RuntimeError(f"iBox {path}: JSON emas: {text[:200]}")


def _session():
    return ClientSession(timeout=ClientTimeout(total=60))


async def _keyset(session, path, **params) -> list:
    out, after = [], None
    for _ in range(200):
        j = await _get(session, path, limit=500, after_id=after, **params)
        out += j.get("data") or []
        meta = j.get("meta") or {}
        if not meta.get("has_more") or not meta.get("next_after_id"):
            break
        after = meta["next_after_id"]
    return out


async def warehouses():
    async with _session() as s:
        return await _keyset(s, "/api/integration/core/warehouse/list")


async def price_types():
    async with _session() as s:
        return await _keyset(s, "/api/integration/product/price-type/list")


# ---------------- qoldiq va narx ----------------
def _num(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict):
        for k in ("value", "amount", "price", "sum"):
            if k in v:
                return _num(v[k])
        return None
    try:
        return float(str(v).replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def _pick(row: dict, keys) -> float | None:
    for k in keys:
        if k in row:
            n = _num(row[k])
            if n is not None:
                return n
    return None


def parse_selection_row(row: dict) -> dict:
    """iBox «stock/selection» qatoridan: product id, sku, qoldiq, narx (javob shakli hujjatlanmagan —
    bir nechta nomni sinab ko'ramiz)."""
    prod = row.get("product") if isinstance(row.get("product"), dict) else {}
    pid = row.get("product_id") or prod.get("id") or row.get("id")
    sku = row.get("sku") or prod.get("sku")
    qty = _pick(row, ("available", "available_quantity", "quantity", "stock", "remain", "remains", "amount",
                      "balance", "count", "qty"))
    price = _pick(row, ("price", "sale_price", "selling_price", "price_value", "product_price"))
    if price is None and isinstance(row.get("prices"), list) and row["prices"]:
        price = _num(row["prices"][0])
    return {"id": pid, "sku": sku, "qty": qty, "price": price,
            "name": row.get("name") or prod.get("name")}


async def selection(session) -> list:
    s = st()
    rows, page = [], 1
    while page <= 200:
        j = await _get(session, "/api/integration/document/stock/selection", warehouse_id=s.get("warehouse_id"),
                       price_type_id=s.get("price_type_id"), per_page=500, page=page)
        data = j.get("data") if isinstance(j, dict) else j
        if isinstance(data, dict) and "data" in data:  # Laravel paginator ichma-ich bo'lishi mumkin
            data = data["data"]
        data = data or []
        rows += data
        meta = (j.get("meta") if isinstance(j, dict) else None) or {}
        last = meta.get("last_page") or (j.get("last_page") if isinstance(j, dict) else None)
        if not data or len(data) < 500 or (last and page >= int(last)):
            break
        page += 1
    return rows


# ---------------- katalog bilan bog'lash ----------------
def catalog_targets() -> list:
    """[(kalit, design, item yoki None, kod, nom)] — iBox bilan bog'lanadigan katalog elementlari."""
    import naborlar
    out = []
    for d in store.designs.values():
        if d.get("deleted"):
            continue
        if naborlar.is_whole(d):
            code = naborlar.fields(d.get("note")).get("kodi") or d["title"].split("·")[-1].strip()
            out.append((f"d:{d['id']}", d, None, code, d["title"]))
        else:
            for c in d.get("categories") or []:
                for it in c["items"]:
                    out.append((f"i:{d['id']}:{it['id']}", d, it, it.get("code", ""), it["name"]))
    return out


def match(products: list) -> tuple[dict, list]:
    """Katalog kaliti -> iBox product. Qaytaradi (bog'langanlar, topilmaganlar)."""
    by_sku, by_name, by_id = {}, {}, {}
    for p in products:
        by_id[str(p.get("id"))] = p
        if p.get("sku"):
            by_sku.setdefault(norm(p["sku"]), p)
        for b in p.get("barcodes") or []:
            by_sku.setdefault(norm(b), p)
        if p.get("name"):
            by_name.setdefault(norm(p["name"]), p)
    links = st()["links"]
    found, missing = {}, []
    for key, d, it, code, name in catalog_targets():
        p = None
        if key in links:
            p = by_id.get(str(links[key]))
        if p is None and norm(code):
            p = by_sku.get(norm(code))
        if p is None:
            p = by_name.get(norm(name)) or by_name.get(norm(f"{d['title']} {name}"))
        if p is not None:
            found[key] = p
        else:
            missing.append((key, d, it, code, name))
    return found, missing


def _fmt_price(n: float, old: str) -> str:
    s = f"{n:.2f}".rstrip("0").rstrip(".")
    unit = "$" if ("$" in (old or "") or not old) else re.sub(r"[\d.,\s]", "", old or "")
    return s + unit


# ---------------- asosiy sinxronlash ----------------
async def sync(report=False) -> str:
    """iBox'dan o'qib, katalogni yangilaydi. Matnli hisobot qaytaradi."""
    if not enabled():
        return "iBox ulanmagan: Render'da IBOX_TOKEN yo'q."
    import naborlar
    import ops
    import publisher
    async with _lock:
        s = st()
        async with _session() as session:
            products = await _keyset(session, "/api/integration/product/list")
            sel = await selection(session)
        stock = {}
        for row in sel:
            r = parse_selection_row(row)
            if r["id"] is not None:
                stock[str(r["id"])] = r
            if r["sku"]:
                stock.setdefault("sku:" + norm(r["sku"]), r)
        found, missing = match(products)
        changed_stock, changed_price, photos, odd = [], [], 0, []
        touched = set()
        for key, p in found.items():
            parts = key.split(":")
            d = store.designs.get(parts[1])
            if not d:
                continue
            it = None
            if parts[0] == "i":
                it = next((x for c in d["categories"] for x in c["items"] if x["id"] == parts[2]), None)
                if it is None:
                    continue
            r = stock.get(str(p.get("id"))) or stock.get("sku:" + norm(p.get("sku")))
            # 1) qoldiq
            if s.get("stock") and r and r["qty"] is not None:
                want = r["qty"] > 0
                cur = (it.get("in_stock", True) if it else d.get("in_stock", True))
                if want != cur:
                    ops.set_stock(d["id"], it["id"] if it else None, want)
                    changed_stock.append(("✅ keldi" if want else "❌ qolmadi") + " — "
                                         + (f"{d['title']} · {it['name']}" if it else d["title"]))
                    touched.add(d["id"])
            # 2) narx (o'chiq bo'lsa — faqat qanday o'zgarishini ko'rsatadi)
            if r and r["price"]:
                label = f"{d['title']} · {it['name']}" if it is not None else d["title"]
                old = it.get("price", "") if it is not None else naborlar.fields(d.get("note")).get("narxi", "")
                new = _fmt_price(r["price"], old)
                oldn = _num(re.sub(r"[^\d.,]", "", old or "")) if old else None
                if norm(new) != norm(old):
                    if oldn and (r["price"] / oldn > 20 or r["price"] / oldn < 0.05):
                        odd.append(f"{label}: katalogda {old}, iBox'da {r['price']:g}")  # valyuta farqi bo'lsa kerak
                    elif s.get("prices"):
                        changed_price.append(f"{label}: {old or '—'} → {new}")
                        if it is not None:
                            it["price"] = new
                        else:
                            f = naborlar.fields(d.get("note"))
                            d["note"] = naborlar.make_note(f.get("jami", ""), new, f.get("kodi", ""))
                        touched.add(d["id"])
                    elif report:
                        changed_price.append(f"{label}: {old or '—'} → {new} (o'zgartirilmadi)")
            # 3) rasm — faqat rasmi yo'q dizaynga, bir marta
            url = p.get("photo_url") or (p.get("photos") or [None])[0]
            if s.get("images") and url and not d.get("photos") and d["id"] not in s["photos_done"]:
                try:
                    await _put_photo(d, url)
                    s["photos_done"][d["id"]] = url
                    photos += 1
                    touched.add(d["id"])
                except Exception as e:  # noqa
                    log.warning("iBox rasm %s: %s", url, e)
        s["last_sync"] = time.time()
        s["last_counts"] = {"ibox": len(products), "linked": len(found), "missing": len(missing)}
        s["missing"] = [[k, (it["name"] if it else d["title"]), code] for k, d, it, code, _ in missing][:300]
        store.save()
        for did in touched:
            publisher.queue_update(did, delay=5)
        lines = []
        if changed_stock:
            lines.append("📦 <b>Qoldiq o'zgardi:</b>\n" + "\n".join("• " + publisher.esc(x) for x in changed_stock[:40]))
        if changed_price:
            lines.append("💰 <b>Narx o'zgardi:</b>\n" + "\n".join("• " + publisher.esc(x) for x in changed_price[:40]))
        if changed_price and not s.get("prices"):
            lines[-1] = lines[-1].replace("💰 <b>Narx o'zgardi:</b>", "💰 <b>iBox narxi boshqacha</b> (narx sinxronlash o'chiq — "
                                          "to'g'ri bo'lsa /ibox → «Narx» ni yoqing):")
        if odd:
            lines.append("⚠️ <b>Narx juda farq qiladi — tegilmadi</b> (valyuta boshqa bo'lishi mumkin, narx turini tekshiring):\n"
                         + "\n".join("• " + publisher.esc(x) for x in odd[:20]))
        if photos:
            lines.append(f"🖼 iBox'dan {photos} ta rasm qo'yildi.")
        head = (f"🔗 iBox: {len(products)} ta mahsulot · katalogda bog'langan: {len(found)} · "
                f"topilmagan: {len(missing)}")
        if report or lines:
            return head + ("\n\n" + "\n\n".join(lines) if lines else "\n\nO'zgarish yo'q.")
        return ""


async def _put_photo(d, url):
    import images
    import ops
    from tg import bot
    headers = {"Authorization": f"Bearer {TOKEN}"}
    async with _session() as s:
        async with s.get(url, headers=headers) as r:
            if r.status != 200:
                raise RuntimeError(f"rasm {r.status}")
            raw = await r.read()
    out = await asyncio.get_event_loop().run_in_executor(None, images.process, raw, True, None)
    owner = store.settings.get("owner_id")
    m = await bot.call("sendPhoto", files={"photo": ("rasm.jpg", out, "image/jpeg")},
                       chat_id=str(owner), disable_notification="true")
    fid = m["photo"][-1]["file_id"]
    await bot.safe_delete(owner, m["message_id"])
    ops.mark_design_photos(d["id"], [fid], False)


async def loop():
    """Fon jarayoni: har 15 daqiqada sinxronlash, o'zgarish bo'lsa egasiga xabar."""
    from tg import bot
    await asyncio.sleep(90)
    fails = 0
    while True:
        if enabled() and st().get("warehouse_id"):
            try:
                text = await sync()
                fails = 0
                owner = store.settings.get("owner_id")
                if text and owner:
                    await bot.send(owner, text, disable_notification=True)
            except Exception as e:  # noqa
                fails += 1
                log.warning("iBox sinxron xato: %s", e)
                if fails in (1, 10) and store.settings.get("owner_id"):
                    try:
                        await bot.send(store.settings["owner_id"], f"⚠️ iBox bilan bog'lanishda xato: {e}")
                    except Exception:  # noqa
                        pass
        await asyncio.sleep(EVERY)


async def sample_text() -> str:
    """Tekshirish uchun: iBox javobidan bitta namuna (dasturchiga ko'rsatish uchun)."""
    async with _session() as session:
        j1 = await _get(session, "/api/integration/product/list", limit=1)
        j2 = await _get(session, "/api/integration/document/stock/selection", warehouse_id=st().get("warehouse_id"),
                        price_type_id=st().get("price_type_id"), per_page=1)
    return json.dumps({"product": j1, "selection": j2}, ensure_ascii=False)[:3500]
