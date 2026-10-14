"""Katalog ustidagi amallar — bot ham, mini ilova ham shularni ishlatadi.
Har bir amal: ma'lumotni o'zgartiradi -> saqlaydi -> guruhdagi postni yangilaydi -> a'zolarni xabardor qiladi."""
import checker
import notify
import publisher
from store import store


class OpError(Exception):
    pass


def _design(design_id) -> dict:
    d = store.designs.get(design_id)
    if not d or d.get("deleted"):
        raise OpError("Dizayn topilmadi")
    return d


def _item(d, item_id):
    c, it = store.find_item(d, item_id)
    if not it:
        raise OpError("Mahsulot topilmadi")
    return c, it


def set_stock(design_id, item_id, in_stock: bool) -> bool:
    d = _design(design_id)
    it = None
    if item_id:
        _, it = _item(d, item_id)
    changed = store.set_stock(d, it, in_stock)
    if changed:
        store.save()
        label = f"{it['name']} ({d['title']})" if it else f"{d['title']} ({store.section(d['section'])['title']})"
        notify.add("keldi" if in_stock else "qolmadi", label)
        publisher.queue_update(design_id)
    return changed


def add_item(design_id, category, name, code, price) -> tuple[dict, list]:
    d = _design(design_id)
    name, code, price, warns = checker.validate_new_item(d, name, code, price)
    if not name:
        raise OpError("Mahsulot nomi bo'sh")
    it = store.add_item(d, category or "Aralash", name, code, price)
    store.save()
    notify.add("new", f"{name} ({d['title']})")
    publisher.queue_update(design_id)
    return it, warns


def edit_item(design_id, item_id, name=None, code=None, price=None, category=None) -> list:
    d = _design(design_id)
    cat, it = _item(d, item_id)
    new_name = it["name"] if name is None else name
    new_code = it.get("code", "") if code is None else code
    new_price = it["price"] if price is None else price
    tmp = dict(d)
    tmp["categories"] = [{"name": c["name"], "items": [x for x in c["items"] if x["id"] != item_id]}
                         for c in d["categories"]]
    new_name, new_code, new_price, warns = checker.validate_new_item(tmp, new_name, new_code, new_price)
    changes = []
    if new_name != it["name"]:
        changes.append(f"nomi: {it['name']} → {new_name}")
    if new_code != it.get("code", ""):
        changes.append(f"kodi: {it.get('code') or '—'} → {new_code or '—'}")
    if new_price != it["price"]:
        changes.append(f"narxi: {it['price']} → {new_price}")
    it.update(name=new_name, code=new_code, price=new_price)
    if category and category.strip().lower() != cat["name"].lower():
        cat["items"].remove(it)
        target = next((c for c in d["categories"] if c["name"].lower() == category.strip().lower()), None)
        if target is None:
            target = {"name": category.strip(), "items": []}
            d["categories"].append(target)
        target["items"].append(it)
        changes.append(f"guruhi: {cat['name']} → {target['name']}")
        if not cat["items"]:
            d["categories"].remove(cat)
    if changes:
        text = f"{new_name} ({d['title']}): " + ", ".join(changes)
        store.event("edit", text, design_id)
        store.save()
        notify.add("edit", text)
        publisher.queue_update(design_id)
    return warns


def delete_item(design_id, item_id):
    d = _design(design_id)
    cat, it = _item(d, item_id)
    cat["items"].remove(it)
    if not cat["items"]:
        d["categories"].remove(cat)
    store.event("delete", f"{it['name']} ({d['title']})", design_id)
    store.save()
    notify.add("edit", f"{it['name']} ({d['title']}) jadvaldan olib tashlandi")
    publisher.queue_update(design_id)


def rename_design(design_id, title):
    d = _design(design_id)
    old = d["title"]
    title = " ".join(title.split())
    if not title or title == old:
        return
    d["title"] = title
    store.event("edit", f"{old} → {title}", design_id)
    store.save()
    publisher.queue_update(design_id)


def set_agents(design_id, agents: list):
    d = _design(design_id)
    d["agents"] = agents
    store.save()
    publisher.queue_update(design_id)


def delete_design(design_id):
    d = _design(design_id)
    d["deleted"] = True
    store.event("delete", d["title"], design_id)
    store.save()
    notify.add("edit", f"{d['title']} katalogdan olib tashlandi")
    publisher.queue_update(design_id, delay=1)
    publisher.queue_index(d["section"])


def mark_design_photos(design_id, photos: list, replace: bool):
    d = _design(design_id)
    d["photos"] = list(photos) if replace else (d.get("photos", []) + list(photos))[:20]
    store.event("edit", f"{d['title']}: rasmlar yangilandi", design_id)
    store.save()
