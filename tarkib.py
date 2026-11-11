"""Nabor tarkibi: har bir nabor bo'limi (33, 39, 44, 46) ichidagi mahsulotlar — bo'limdagi barcha naborlar
uchun bir xil. /tarkib → bo'limni tanlash → matn bilan yuborish:
    Pela - 6
    Kosa 5.5 - 6
    Narxi: 105$
    Jami: 44
Saqlangach guruhdagi shu bo'lim postlari yangilanadi."""
import html
import re

import publisher
from store import store
from tg import bot


def _sections():
    return [s for s in store.s["sections"] if s["kind"] == "nabor"]


def _tpl(key):
    return store.s.setdefault("nabor_templates", {}).setdefault(key, {"pieces": "", "price": "", "items": []})


def _show(key) -> str:
    t = _tpl(key)
    rows = "\n".join(f"• {html.escape(n)} — {html.escape(str(q))}" for n, q in t.get("items") or []) or "— hali yozilmagan —"
    return (f"<b>{html.escape(store.section(key)['title'])}</b>\n"
            f"Jami: {html.escape(str(t.get('pieces', '')))} ta · Narxi: {html.escape(str(t.get('price', '')))}\n\n{rows}")


async def start(uid):
    rows = [[{"text": s["title"], "callback_data": f"tk:{s['key']}"}] for s in _sections()]
    await bot.send(uid, "📦 Qaysi naborning tarkibini yozamiz?", reply_markup={"inline_keyboard": rows})


async def pick(uid, key, sessions):
    if not store.section(key):
        return
    sessions[uid] = {"await": "tarkib", "key": key}
    await bot.send(uid, _show(key) + "\n\n✍️ Yangi tarkibni bitta xabarda yuboring, har qatorga bittadan:\n"
                   "<code>Pela - 6\nKosa 5.5 - 6\nChoynak - 1\nNarxi: 105$\nJami: 44</code>\n\n"
                   "(Narxi va Jami qatorlari ixtiyoriy. Bekor qilish: /bekor)")


def parse(text):
    items, price, pieces = [], None, None
    for line in text.splitlines():
        line = line.strip(" •-\t")
        if not line:
            continue
        m = re.match(r"^narx[a-z]*\s*[:\-]?\s*(.+)$", line, re.I)
        if m:
            price = m.group(1).strip()
            if re.fullmatch(r"[\d.,]+", price):
                price += "$"
            continue
        m = re.match(r"^jami\s*[:\-]?\s*(\d+)", line, re.I)
        if m:
            pieces = m.group(1)
            continue
        m = re.match(r"^(.+?)\s*(?:[-–—:=x×]|\s)\s*(\d+)\s*(?:ta|dona|шт)?$", line, re.I)
        if m:
            items.append([m.group(1).strip(" -–—:"), m.group(2) + "ta"])
        else:
            items.append([line, ""])
    return items, price, pieces


async def save(uid, sess, text, sessions):
    key = sess["key"]
    items, price, pieces = parse(text)
    if not items and not price and not pieces:
        await bot.send(uid, "Hech narsa topilmadi. Har qatorga: <code>Nomi - soni</code>")
        return
    t = _tpl(key)
    if items:
        t["items"] = items
        if not pieces:
            try:
                pieces = str(sum(int(re.sub(r"\D", "", q) or 0) for _, q in items))
            except ValueError:
                pass
    if price:
        t["price"] = price
    if pieces:
        t["pieces"] = pieces
    store.save()
    sessions.pop(uid, None)
    for i, d in enumerate(store.designs_in(key)):
        publisher.queue_update(d["id"], delay=5 + i * 3)
    await bot.send(uid, "✅ Saqlandi.\n\n" + _show(key) + "\n\nGuruhdagi shu bo'lim postlari bir necha daqiqada yangilanadi.")
