"""Botdagi qo'shimcha imkoniyatlar (katalogdagi kabi):
- aqlli qidiruv ("za 5" -> Zaftrik 5, "01-AL ta to 9" -> shu dizayn ichidan)
- mahsulot kartochkasi (rasm, narx, qolmadi/keldi, tahrirlash, rasm qo'yish)
- mavjud mahsulotlardan tanlab qo'shish
- mahsulotlar tartibini o'zgartirish"""
import html
import re

import ops
from store import store
from tg import bot, TelegramError

AWAITS = ("search", "pick_q", "reorder")
PICK_PAGE = 10


def esc(t):
    return html.escape(str(t or ""), quote=False)


def btn(text, data):
    return {"text": text[:60], "callback_data": data}


def ikb(rows):
    return {"inline_keyboard": rows}


# ---------------- aqlli qidiruv ----------------
def nrm(t):
    return re.sub(r"[‘’`ʻʼ]", "'", str(t or "").lower()).replace(",", ".")


def match_q(q, name, code=""):
    """Har bir so'z mahsulot nomidagi so'z boshiga mos kelishi kerak: 'ta to 9' -> 'Tar tosh 9'."""
    c = nrm(code)
    flat = re.sub(r"\s+", "", nrm(q))
    if c and len(flat) >= 3 and flat in c:
        return 6
    toks = re.sub(r"([a-z'])(\d)", r"\1 \2", nrm(q)).split()
    if not toks:
        return 0
    nm = nrm(name)
    words = [w for w in re.split(r"[\s\-/+]+", nm) if w]
    score = 0
    for t in toks:
        if c and len(t) >= 3 and t in c:
            score += 3
            continue
        hit = 0
        for w in words:
            if w == t:
                hit = 3
                break
            if w.startswith(t):
                hit = max(hit, 2)
        if not hit and len(t) >= 3 and t in nm:
            hit = 1
        if not hit:
            return 0
        score += hit
    return score


def search(q):
    """(dizayn, mahsulot|None, ball) ro'yxati. Birinchi so'z dizayn nomi bo'lsa — shu dizayn ichidan."""
    toks = q.split()
    scope = None
    if len(toks) >= 2:
        d = store.find_design(toks[0])
        if d:
            scope, q = d, " ".join(toks[1:])
    out = []
    designs = [scope] if scope else [d for d in store.designs.values() if not d.get("deleted")]
    for d in designs:
        if not scope:
            sc = match_q(q, d["title"])
            if sc:
                out.append((d, None, sc + 1))
        for c in d["categories"]:
            for it in c["items"]:
                sc = match_q(q, it["name"], it.get("code", ""))
                if sc:
                    out.append((d, it, sc))
    out.sort(key=lambda x: (-x[2], x[0].get("order", 0)))
    return out, scope


def short(d):
    return d["title"].replace("LENON ", "").replace("BODEN ", "B-")


async def ask_search(uid, sessions):
    sessions[uid] = {"await": "search"}
    await bot.send(uid, "🔍 Nima qidiramiz? Qisqa yozsangiz ham bo'ladi:\n"
                        "<code>za 5</code> → Zaftrik 5\n<code>ta to 9</code> → Tar tosh 9\n"
                        "<code>01-AL ta 9</code> → faqat 01-AL ichidan\n<code>YA4254</code> → kod bo'yicha")


async def show_search(uid, q):
    res, scope = search(q)
    if not res:
        await bot.send(uid, f"🔍 «{esc(q)}» — hech narsa topilmadi.")
        return
    where = f" ({esc(scope['title'])} ichidan)" if scope else ""
    rows = []
    for d, it, _ in res[:24]:
        if it:
            mark = "❌ " if not it.get("in_stock", True) else ("🖼 " if it.get("photo") else "")
            rows.append([btn(f"{mark}{it['name']} · {short(d)} · {it['price']}", f"sr:{d['id']}:{it['id']}")])
        else:
            rows.append([btn(f"📁 {d['title']}", f"sr:{d['id']}:-")])
    more = f"\n… yana {len(res) - 24} ta. Aniqroq yozing (masalan dizayn nomi bilan: <code>01-AL {esc(q)}</code>)" if len(res) > 24 else ""
    await bot.send(uid, f"🔍 «{esc(q)}»{where}: <b>{len(res)}</b> ta natija{more}\n"
                        "Mahsulotni bosing — rasmi va narxi chiqadi.", reply_markup=ikb(rows))


def card_markup(d, it):
    rows = [[btn("✅ Keldi deb belgilash", f"sr:k:{d['id']}:{it['id']}") if not it.get("in_stock", True)
             else btn("❌ Qolmadi deb belgilash", f"sr:q:{d['id']}:{it['id']}")],
            [btn("💲 Narx", f"ed:f:{d['id']}:{it['id']}:price"), btn("🔤 Nom", f"ed:f:{d['id']}:{it['id']}:name"),
             btn("🏷 Kod", f"ed:f:{d['id']}:{it['id']}:code")],
            [btn("📷 Rasm qo'yish" if not it.get("photo") else "📷 Rasmni almashtirish", f"sr:ph:{d['id']}:{it['id']}")]]
    return ikb(rows)


def card_caption(d, it):
    st = "❌ <b>QOLMADI</b>" if not it.get("in_stock", True) else "✅ Bor"
    cat = next((c["name"] for c in d["categories"] if it in c["items"]), "")
    return (f"<b>{esc(it['name'])}</b>\nKodi: <code>{esc(it.get('code') or '—')}</code>\nNarxi: <b>{esc(it['price'])}</b>\n"
            f"{esc(d['title'])} · {esc(cat)}\nHolati: {st}")


async def send_card(uid, d, it):
    if it.get("photo"):
        await bot.call("sendPhoto", chat_id=uid, photo=it["photo"], caption=card_caption(d, it),
                       parse_mode="HTML", reply_markup=card_markup(d, it))
    else:
        await bot.send(uid, "🖼 <i>rasmi hali yo'q</i>\n\n" + card_caption(d, it), reply_markup=card_markup(d, it))


async def search_callback(uid, data, cb, sessions):
    p = data.split(":")
    await bot.answer_cb(cb["id"])
    if p[1] in ("q", "k"):
        d = store.designs.get(p[2])
        _, it = store.find_item(d, p[3]) if d else (None, None)
        if not it:
            return
        ops.set_stock(d["id"], it["id"], p[1] == "k")
        m = cb["message"]
        try:
            if m.get("photo"):
                await bot.call("editMessageCaption", chat_id=uid, message_id=m["message_id"], caption=card_caption(d, it),
                               parse_mode="HTML", reply_markup=card_markup(d, it))
            else:
                await bot.call("editMessageText", chat_id=uid, message_id=m["message_id"],
                               text="🖼 <i>rasmi hali yo'q</i>\n\n" + card_caption(d, it), parse_mode="HTML",
                               reply_markup=card_markup(d, it))
        except TelegramError:
            pass
        return
    if p[1] == "ph":
        d = store.designs.get(p[2])
        _, it = store.find_item(d, p[3]) if d else (None, None)
        if not it:
            return
        sessions[uid] = {"photo_for": (d["id"], it["id"])}
        await bot.send(uid, f"📷 <b>{esc(it['name'])}</b> ({esc(d['title'])}) uchun rasm yuboring.\n"
                            "Bot uni tahrirlaydi va logo qo'yadi.")
        return
    d = store.designs.get(p[1])
    if not d:
        return
    if p[2] == "-":
        n = sum(len(c["items"]) for c in d["categories"])
        out = sum(1 for c in d["categories"] for i in c["items"] if not i.get("in_stock", True))
        await bot.send(uid, f"📁 <b>{esc(d['title'])}</b>\n{n} ta mahsulot · {out} tasi qolmagan\n"
                            f"Shu dizayn ichidan qidirish: <code>{esc(short(d))} ...</code>")
        return
    _, it = store.find_item(d, p[2])
    if it:
        await send_card(uid, d, it)


# ---------------- mavjud mahsulotlardan tanlash ----------------
def build_pick_list(d):
    have = {(i["name"].lower(), i.get("code", "")) for c in d["categories"] for i in c["items"]}
    agg = {}
    for x in store.designs_in(d["section"]):
        for c in x["categories"]:
            for it in c["items"]:
                k = (it["name"].lower(), it.get("code", ""))
                if k in have:
                    continue
                e = agg.setdefault(k, {"name": it["name"], "code": it.get("code", ""), "prices": {}, "cats": {}})
                e["prices"][it["price"]] = e["prices"].get(it["price"], 0) + 1
                e["cats"][c["name"]] = e["cats"].get(c["name"], 0) + 1
    top = lambda o: max(o.items(), key=lambda kv: kv[1])[0] if o else ""
    lst = [{"name": e["name"], "code": e["code"], "price": top(e["prices"]), "category": top(e["cats"])} for e in agg.values()]
    lst.sort(key=lambda x: (x["category"], [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", x["name"].lower())]))
    return lst


def pick_view(sess):
    d = store.designs[sess["design"]]
    lst, sel, q = sess["list"], sess["sel"], sess.get("q", "")
    idx = [i for i, x in enumerate(lst) if not q or match_q(q, x["name"], x["code"])]
    pages = max(1, (len(idx) + PICK_PAGE - 1) // PICK_PAGE)
    sess["page"] = min(sess.get("page", 0), pages - 1)
    chunk = idx[sess["page"] * PICK_PAGE:(sess["page"] + 1) * PICK_PAGE]
    rows = [[btn(("✅ " if i in sel else "▫️ ") + f"{lst[i]['name']} · {lst[i]['price']}", f"pk:t:{i}")] for i in chunk]
    nav = []
    if sess["page"] > 0:
        nav.append(btn("⬅️", "pk:p:-1"))
    nav.append(btn(f"{sess['page'] + 1}/{pages}", "noop"))
    if sess["page"] < pages - 1:
        nav.append(btn("➡️", "pk:p:1"))
    rows.append(nav)
    rows.append([btn("☑️ Ko'ringanlarni belgilash", "pk:all")] + ([btn("🔄 Qidiruvni tozalash", "pk:clr")] if q else []))
    rows.append([btn(f"➕ Qo'shish ({len(sel)})", "pk:go"), btn("❌ Bekor", "cancel")])
    head = (f"📚 <b>{esc(d['title'])}</b> ga mavjud mahsulotlardan qo'shish\n"
            f"Bo'limda {len(lst)} ta mahsulot" + (f" · «{esc(q)}» bo'yicha {len(idx)} ta" if q else "") +
            f" · tanlangan: <b>{len(sel)}</b>\n\n🔍 Qidirish uchun shunchaki yozing (masalan <code>ta to 9</code>).")
    return head, ikb(rows)


async def start_pick(uid, d, sessions):
    lst = build_pick_list(d)
    if not lst:
        await bot.send(uid, "Bu dizaynda bo'limdagi barcha mahsulotlar allaqachon bor.")
        return
    sess = {"await": "pick_q", "design": d["id"], "list": lst, "sel": set(), "q": "", "page": 0}
    sessions[uid] = sess
    text, kb = pick_view(sess)
    m = await bot.send(uid, text, reply_markup=kb)
    sess["msg"] = m["message_id"]


async def redraw_pick(uid, sess, new=False):
    text, kb = pick_view(sess)
    if not new and sess.get("msg"):
        try:
            await bot.call("editMessageText", chat_id=uid, message_id=sess["msg"], text=text, parse_mode="HTML",
                           reply_markup=kb)
            return
        except TelegramError as e:
            if "not modified" in e.description:
                return
    m = await bot.send(uid, text, reply_markup=kb)
    sess["msg"] = m["message_id"]


async def pick_callback(uid, data, cb, sessions, main_menu):
    sess = sessions.get(uid)
    if not sess or "list" not in sess:
        await bot.answer_cb(cb["id"], "Sessiya eskirgan, qaytadan boshlang.", True)
        return
    p = data.split(":")
    if p[1] == "t":
        i = int(p[2])
        sess["sel"].symmetric_difference_update({i})
        await bot.answer_cb(cb["id"])
    elif p[1] == "p":
        sess["page"] = max(0, sess.get("page", 0) + int(p[2]))
        await bot.answer_cb(cb["id"])
    elif p[1] == "all":
        q = sess.get("q", "")
        sess["sel"].update(i for i, x in enumerate(sess["list"]) if not q or match_q(q, x["name"], x["code"]))
        await bot.answer_cb(cb["id"], "Belgilandi")
    elif p[1] == "clr":
        sess["q"] = ""
        await bot.answer_cb(cb["id"])
    elif p[1] == "go":
        if not sess["sel"]:
            await bot.answer_cb(cb["id"], "Hech narsa tanlanmagan", True)
            return
        items = [sess["list"][i] for i in sorted(sess["sel"])]
        n, warns = ops.add_items(sess["design"], items)
        d = store.designs[sess["design"]]
        sessions.pop(uid, None)
        await bot.answer_cb(cb["id"], f"{n} ta qo'shildi")
        msg = f"✅ <b>{esc(d['title'])}</b> ga {n} ta mahsulot qo'shildi (NEW belgisi bilan). Jadval yangilanadi."
        if warns:
            msg += "\n\n⚠️ " + "\n".join(esc(w) for w in warns[:10])
        await bot.send(uid, msg, reply_markup=main_menu())
        return
    await redraw_pick(uid, sess)


# ---------------- tartibni o'zgartirish ----------------
def flat_items(d):
    return [(c["name"], it) for c in d["categories"] for it in c["items"]]


async def show_order(uid, d, sessions):
    rows, n, last = [], 0, None
    for cat, it in flat_items(d):
        if cat != last and cat != "Asosiy":
            rows.append(f"\n<b>— {esc(cat)} —</b>")
        last = cat
        n += 1
        rows.append(f"{n}. {esc(it['name'])}")
    sessions[uid] = {"await": "reorder", "design": d["id"]}
    text = (f"↕️ <b>{esc(d['title'])}</b> — hozirgi tartib:\n" + "\n".join(rows) +
            "\n\n✍️ Qaysi raqamni qaysi o'ringa? Masalan:\n<code>3 1</code> — 3-mahsulot 1-o'ringa chiqadi\n"
            "Bir nechtasini birdan ham yozish mumkin (har biri yangi qatorda). Tugatish: /menu")
    if len(text) > 4000:
        text = text[:3900] + "\n…\n\n✍️ Masalan: <code>3 1</code> — 3-mahsulot 1-o'ringa. Tugatish: /menu"
    await bot.send(uid, text)


def move_layout(d, frm, to):
    """frm-raqamli mahsulotni to-o'ringa qo'yadi; u o'sha joydagi mahsulotning guruhiga o'tadi."""
    flat = flat_items(d)
    if not (1 <= frm <= len(flat)) or not (1 <= to <= len(flat)):
        raise ops.OpError(f"Raqam 1 dan {len(flat)} gacha bo'lishi kerak")
    cat_of = {it["id"]: c for c, it in flat}
    _, moving = flat[frm - 1]
    rest = [x for x in flat if x[1]["id"] != moving["id"]]
    target_cat = flat[to - 1][0] if to - 1 < len(flat) else rest[-1][0]
    rest.insert(to - 1, (target_cat, moving))
    cat_of[moving["id"]] = target_cat
    layout, last = [], object()
    for c, it in rest:
        c = cat_of[it["id"]]
        if c != last:
            layout.append({"cat": c})
            last = c
        layout.append({"item": it["id"]})
    return layout


async def reorder_text(uid, sess, text, sessions):
    d = store.designs.get(sess["design"])
    pairs = re.findall(r"(\d+)\s*(?:->|→|-|\s)\s*(\d+)", text)
    if not d or not pairs:
        await bot.send(uid, "Tushunmadim. Masalan: <code>3 1</code> (3-mahsulotni 1-o'ringa). Tugatish: /menu")
        return
    try:
        for a, b in pairs:
            ops.reorder(d["id"], move_layout(d, int(a), int(b)))
    except ops.OpError as e:
        await bot.send(uid, f"⚠️ {esc(e)}")
        return
    await bot.send(uid, f"✅ Saqlandi ({len(pairs)} ta o'zgarish). Katalog va jadval yangilanadi.")
    await show_order(uid, d, sessions)
