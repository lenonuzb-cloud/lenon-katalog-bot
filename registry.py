"""9-agent: Ro'yxatchi — mavjud mahsulotlar ro'yxatini yuritadi.

- LENON (01-AL ... 14-NL va keyingi barcha dizaynlar) va BODEN bo'limlaridagi barcha mahsulotlarni
  bitta umumiy ro'yxatga yig'adi (nomi, kodi, narxi, guruhi). Yangi mahsulot istalgan dizaynga
  qo'shilsa — avtomatik ro'yxatga ham tushadi.
- Bitta dizayn ichida mahsulot takrorlanishiga yo'l qo'ymaydi.
Ro'yxat har safar dizaynlardan qayta yig'iladi, shuning uchun u doim aniq bo'ladi
(o'chirilgan dizaynlardagi mahsulotlar ham ro'yxatda qoladi)."""
import re
from collections import Counter

from store import store


def _norm(t):
    return re.sub(r"\s+", " ", str(t or "").strip().lower())


def hkey(name, code):
    """Ro'yxatdan yashirish uchun kalit: nomi|kodi."""
    return _norm(name) + "|" + str(code or "").strip().upper()


def key(name, code, category):
    return (_norm(name), str(code or "").strip().upper(), _norm(category))


def _sort_key(name):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", _norm(name))]


def products(section_key) -> list:
    """Bo'limning umumiy mahsulotlar ro'yxati — jadvallardagi tartibda (guruh, keyin mahsulot)."""
    entries, order_cat, prices, cats = {}, [], {}, {}
    designs = sorted((d for d in store.designs.values() if d["section"] == section_key),
                     key=lambda d: (d.get("deleted", False), d.get("order", 0), d.get("created", 0)))
    for d in designs:
        for c in d["categories"]:
            if _norm(c["name"]) not in order_cat:
                order_cat.append(_norm(c["name"]))
            for it in c["items"]:
                k = key(it["name"], it.get("code", ""), "")  # ro'yxatda nomi+kodi bo'yicha bitta
                if k not in entries:
                    entries[k] = {"name": it["name"], "code": it.get("code", ""), "category": c["name"],
                                  "photo": "", "designs": 0, "n": len(entries)}
                    prices[k] = Counter()
                    cats[k] = Counter()
                cats[k][c["name"]] += 1
                e = entries[k]
                if not d.get("deleted"):
                    e["designs"] += 1
                prices[k][it["price"]] += 1
                if not e["photo"] and it.get("photo"):
                    e["photo"] = it["photo"]
    hidden = set(store.settings.get("registry_hidden", {}).get(section_key, []))
    out = []
    for k, e in entries.items():
        if hkey(e["name"], e["code"]) in hidden:
            continue
        pc = prices[k].most_common()
        e["price"] = pc[0][0] if pc else ""
        e["prices"] = [p for p, _ in pc]
        e["category"] = cats[k].most_common(1)[0][0]
        out.append(e)
    out.sort(key=lambda e: (order_cat.index(_norm(e["category"])) if _norm(e["category"]) in order_cat else 999, e["n"]))
    for e in out:
        e.pop("n", None)
    return out


def all_registries() -> dict:
    return {s["key"]: products(s["key"]) for s in store.s["sections"] if s["kind"] == "design"}


def known(section_key, name, code, category=None) -> bool:
    k = key(name, code, "")
    return any(key(e["name"], e["code"], "") == k for e in products(section_key))


def in_design(design, name, code) -> bool:
    k = key(name, code, "")
    return any(key(it["name"], it.get("code", ""), "") == k for c in design["categories"] for it in c["items"])


def find_duplicate(design, name, code, category, exclude_id=None):
    """Shu dizaynda xuddi shu mahsulot (nomi + kodi + guruhi bir xil) bormi?"""
    k = key(name, code, category)
    for c in design["categories"]:
        for it in c["items"]:
            if it["id"] != exclude_id and key(it["name"], it.get("code", ""), c["name"]) == k:
                return it
    return None


def duplicates_in(design) -> list:
    seen, dups = set(), []
    for c in design["categories"]:
        for it in c["items"]:
            k = key(it["name"], it.get("code", ""), c["name"])
            if k in seen:
                dups.append(f"{design['title']}: «{it['name']}» ({c['name']}) ikki marta yozilgan")
            seen.add(k)
    return dups
