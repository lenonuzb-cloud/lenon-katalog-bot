"""3-agent: guruhga joylash va jadvallarni yangilab turish.

Asosiy usul — Telegram'ning "boy" (rich) xabarlari: rasm slayd-shousi + jadval + tugmalar,
xuddi guruhdagi hozirgi postlar kabi. Agar ular ishlamasa — zaxira usul:
rasmlar albomi + jadval rasmi."""
import asyncio
import html
import logging

import config
import images
from store import store, now
from tg import bot, TelegramError

log = logging.getLogger("publisher")

_locks: dict[str, asyncio.Lock] = {}
_pending: dict[str, asyncio.Task] = {}
error_hook = None  # checker.report_error


class NotConfigured(Exception):
    pass


def esc(t) -> str:
    return html.escape(str(t or ""), quote=True)


def _lock(key) -> asyncio.Lock:
    if key not in _locks:
        _locks[key] = asyncio.Lock()
    return _locks[key]


def _target(section_key):
    st = store.settings
    gid = st.get("group_id")
    thread = st["topics"].get(section_key)
    if not gid or not thread:
        name = config.TOPIC_KEYS.get(section_key, section_key)
        raise NotConfigured(f"«{name}» bo'limi hali sozlanmagan. Guruhda o'sha bo'limga /bolim {section_key} deb yozing.")
    return gid, thread


def item_state(obj) -> str:
    t = now()
    if not obj.get("in_stock", True):
        return "qolmadi"
    if obj.get("new_until", 0) > t:
        return "new"
    if obj.get("keldi_until", 0) > t:
        return "keldi"
    return ""


def catalog_url(design_id=None) -> str:
    """Jadval tagidagi «📒 Katalog» havolasi — aynan shu dizaynni ochadi.
    BotFather'da Mini App yoqilgan bo'lsa Telegram ichida, aks holda brauzerda ochiladi."""
    if bot.me.get("has_main_web_app") or not config.PUBLIC_URL:
        u = f"https://t.me/{bot.username}?startapp"
        return u + (f"=d_{design_id}" if design_id else "")
    return f"{config.PUBLIC_URL}/app" + (f"?d={design_id}" if design_id else "")


def public_links() -> bool:
    """Katalog hamma uchun ochiq bo'lsa True. Maxfiy rejimda guruhga katalog havolalari qo'yilmaydi."""
    return bool(bot.username)  # guruh a'zolari katalogni ochishi mumkin — havolalar doim turadi


def keyboard(d) -> dict | None:
    rows = []
    agents = []
    for a in d.get("agents", []):
        user = store.settings["agents"].get(a)
        if user:
            agents.append({"text": a, "url": f"https://t.me/{user}"})
    if agents:
        rows.append(agents)
    if public_links():
        rows.append([{"text": f"📒 {d['title']} — katalogda ochish", "url": catalog_url(d["id"])}])
    return {"inline_keyboard": rows} if rows else None


# ---------------- HTML quruvchilar ----------------
def _slideshow(d, caption) -> tuple[str, list]:
    media = []
    if not d.get("photos"):
        return "", media
    imgs = ""
    for i, fid in enumerate(d["photos"][:20]):
        mid = f"p{i}"
        media.append({"id": mid, "media": {"type": "photo", "media": fid}})
        imgs += f'<img src="tg://photo?id={mid}"/>'
    tag = "tg-slideshow" if len(media) > 1 else "figure"
    return f"<{tag}>{imgs}<figcaption>{esc(caption)}</figcaption></{tag}>", media


def item_url(d, it) -> str:
    """Jadvaldagi mahsulot nomi havolasi — mahsulot rasmini yengil sahifada tez ochadi."""
    if config.PUBLIC_URL:
        return f"{config.PUBLIC_URL}/p/{d['id']}/{it['id']}"
    if bot.me.get("has_main_web_app"):
        return f"https://t.me/{bot.username}?startapp=i_{d['id']}_{it['id']}"
    return f"{config.PUBLIC_URL}/app?d={d['id']}&i={it['id']}"


def _links_on() -> bool:
    return bool(bot.username) and store.settings.get("item_links", True)


def _name_cell(it, d=None) -> str:
    s = item_state(it)
    n = esc(it["name"])
    if d is not None and _links_on():
        n = f'<a href="{esc(item_url(d, it))}">{n}</a>'

    if s == "qolmadi":
        return f"<s>{n}</s> ❌ <b>Qolmadi</b>"
    if s == "new":
        return f"{n} 🆕"
    if s == "keldi":
        return f"{n} ✅ <b>Keldi</b>"
    return n


def design_html(d) -> tuple[str, list]:
    parts = []
    show, media = _slideshow(d, d["title"])
    if show:
        parts.append(show)
    parts.append(f"<blockquote><i>{esc(d['title'])}</i></blockquote>")
    if item_state(d) == "new":
        parts.append("<p>🆕 <b>Yangi dizayn!</b></p>")
    has_code = any(it.get("code") for c in d["categories"] for it in c["items"])
    ncol = 4 if has_code else 3
    rows = ["<tr><th>No.</th><th>Nomi</th>" + ("<th>Kodi</th>" if has_code else "") + "<th>Narxi</th></tr>"]
    no = 0
    for c in d["categories"]:
        if c["name"] and c["name"] != "Asosiy":
            rows.append(f'<tr><th colspan="{ncol}" align="center">{esc(c["name"])}</th></tr>')
        for it in c["items"]:
            no += 1
            code = f"<td>{esc(it['code'])}</td>" if has_code else ""
            rows.append(f'<tr><td align="center">{no}</td><td>{_name_cell(it, d)}</td>{code}<td>{esc(it["price"])}</td></tr>')
    parts.append("<table bordered striped compact>" + "".join(rows) + "</table>")
    if d.get("note"):
        parts.append(f"<p><b>{esc(d['note'])}</b></p>")
    return "".join(parts), media


def nabor_html(d) -> tuple[str, list]:
    tpl = store.s["nabor_templates"].get(d["section"]) or {}
    parts = []
    show, media = _slideshow(d, d["title"])
    if show:
        parts.append(show)
    parts.append("<p>꧁👑 <b>LENON</b> 👑꧂</p>")
    s = item_state(d)
    if s == "qolmadi":
        parts.append("<h3>❌ QOLMADI</h3>")
    elif s == "new":
        parts.append("<h3>🆕 YANGI</h3>")
    elif s == "keldi":
        parts.append("<h3>✅ YANA KELDI</h3>")
    parts.append(f"<p><b>Nabor:</b> {esc(d['title'])}<br><b>Jami:</b> {esc(tpl.get('pieces', ''))}ta<br>"
                 f"<b>Narxi:</b> {esc(tpl.get('price', ''))}</p>")
    if tpl.get("items"):
        rows = "".join(f"<tr><td>{esc(n)}</td><td align=\"right\">{esc(q)}</td></tr>" for n, q in tpl["items"])
        parts.append(f"<table bordered striped compact>{rows}</table>")
    return "".join(parts), media


def index_html(section_key) -> str:
    sec = store.section(section_key)
    ds = store.designs_in(section_key)
    lines = []
    for d in ds:
        s = item_state(d)
        mark = {"qolmadi": "❌", "new": "🆕", "keldi": "✅"}.get(s, "▫️")
        link = store.post_link(d)
        name = f'<a href="{esc(link)}">{esc(d["title"])}</a>' if link and s != "qolmadi" else esc(d["title"])
        lines.append(f"{mark} {name}")
    head = "꧁👑 <b>LENON</b> 👑꧂" if section_key.startswith("nabor") else f"<b>{esc(sec['title'])}</b>"
    return f"<p>{head}</p><p>" + "<br>".join(lines) + "</p>"


# ---------------- zaxira (oddiy) ko'rinish ----------------
def design_table_rows(d):
    rows = []
    no = 0
    for c in d["categories"]:
        if c["name"] and c["name"] != "Asosiy":
            rows.append(("cat", c["name"]))
        for it in c["items"]:
            no += 1
            rows.append(("item", no, it["name"], it.get("code", ""), it["price"], item_state(it)))
    return rows


def nabor_caption(d) -> str:
    tpl = store.s["nabor_templates"].get(d["section"]) or {}
    s = item_state(d)
    head = {"qolmadi": "❌ <b>QOLMADI</b>\n", "new": "🆕 <b>YANGI</b>\n", "keldi": "✅ <b>YANA KELDI</b>\n"}.get(s, "")
    lines = "\n".join(f"{esc(n)} — {esc(q)}" for n, q in tpl.get("items", []))
    return (f"꧁👑 <b>LENON</b> 👑꧂\n{head}\n<b>Nabor:</b> {esc(d['title'])}\n<b>Jami:</b> {esc(tpl.get('pieces',''))}ta\n"
            f"<b>Narxi:</b> {esc(tpl.get('price',''))}\n\n{lines}")


def index_text(section_key) -> str:
    sec = store.section(section_key)
    out = []
    for d in store.designs_in(section_key):
        s = item_state(d)
        mark = {"qolmadi": "❌", "new": "🆕", "keldi": "✅"}.get(s, "▫️")
        link = store.post_link(d)
        out.append(f'{mark} <a href="{esc(link)}">{esc(d["title"])}</a>' if link else f"{mark} {esc(d['title'])}")
    return f"<b>{esc(sec['title'])}</b>\n\n" + "\n".join(out)


# ---------------- yuborish / tahrirlash ----------------
def _rich_allowed() -> bool:
    return config.RICH_MODE != "off" and store.settings.get("rich_ok") is not False


def _links_rejected(e) -> bool:
    desc = str(getattr(e, "description", e)).lower()
    return store.settings.get("item_links", True) and any(
        w in desc for w in ("parse", "tag", "href", "url", "entity", "unsupported", "can't find end"))


def _strip_links(html_text: str) -> str:
    import re
    return re.sub(r'<a href="[^"]*">(.*?)</a>', r"\1", html_text)


async def _send_rich(chat_id, thread, html_text, media, markup):
    try:
        return await _send_rich0(chat_id, thread, html_text, media, markup)
    except TelegramError as e:
        if "<a href" in html_text and _links_rejected(e):
            store.settings["item_links"] = False
            store.save()
            log.warning("Jadvaldagi havolalar qabul qilinmadi, havolasiz yuboriladi: %s", e)
            return await _send_rich0(chat_id, thread, _strip_links(html_text), media, markup)
        raise


async def _edit_rich(chat_id, message_id, html_text, media, markup):
    try:
        return await _edit_rich0(chat_id, message_id, html_text, media, markup)
    except TelegramError as e:
        if "<a href" in html_text and _links_rejected(e):
            store.settings["item_links"] = False
            store.save()
            log.warning("Jadvaldagi havolalar qabul qilinmadi, havolasiz tahrirlanadi: %s", e)
            return await _edit_rich0(chat_id, message_id, _strip_links(html_text), media, markup)
        raise


async def _send_rich0(chat_id, thread, html_text, media, markup):
    rm = {"html": html_text}
    if media:
        rm["media"] = media
    return await bot.call("sendRichMessage", chat_id=chat_id, message_thread_id=thread,
                          rich_message=rm, reply_markup=markup)


async def _edit_rich0(chat_id, message_id, html_text, media, markup):
    rm = {"html": html_text}
    if media:
        rm["media"] = media
    return await bot.call("editMessageText", chat_id=chat_id, message_id=message_id,
                          rich_message=rm, reply_markup=markup)


def _note_rich_failure(e: TelegramError):
    desc = e.description.lower()
    if e.code == 404 or "method not found" in desc or "unknown method" in desc:
        store.settings["rich_ok"] = False
        store.save()


async def _report(context, e):
    log.error("%s: %s", context, e)
    if error_hook:
        await error_hook(context, e)


async def _delete_post(d):
    m = d.get("msg")
    if not m:
        return
    await bot.safe_delete(m["chat_id"], m["message_id"])
    for mid in m.get("extra_ids", []):
        await bot.safe_delete(m["chat_id"], mid)
    d["msg"] = None
    pm = d.pop("pdf_msg", None)
    if pm:
        await bot.safe_delete(pm["chat_id"], pm["message_id"])


async def publish(design_id: str, repost: bool = False):
    """Dizaynni guruhga joylaydi (yoki mavjud postni yangilaydi)."""
    d = store.designs.get(design_id)
    if not d:
        return
    async with _lock(design_id):
        if d.get("deleted"):
            await _delete_post(d)
            store.save()
            return
        gid, thread = _target(d["section"])
        if repost:
            await _delete_post(d)
        kind = store.section(d["section"])["kind"]
        updated = False
        if d.get("msg"):
            updated = await _update(d, kind)
            if not updated:
                await _delete_post(d)
        if not updated:
            await _post_new(d, kind, gid, thread)
            # jadval tagiga — shu jadvalning PDF katalogi (alohida fayl bo'lib)
            if kind == "design":
                try:
                    await _post_pdf(d, gid, thread)
                except Exception as e:  # noqa
                    await _report(f"{d['title']}: PDF jadval tagiga joylanmadi", e)
        store.save()
    if updated:
        if kind == "design":
            queue_pdf(design_id)
        return
    if kind in ("design", "nabor") and d["section"] != "lenon":
        queue_index(d["section"])


# ---------------- jadval tagidagi PDF fayl ----------------
async def _post_pdf(d, gid, thread):
    import webapi
    pdf, name = await webapi.cached_pdf(d["id"])
    reply = {"message_id": d["msg"]["message_id"], "allow_sending_without_reply": True} if d.get("msg") else None
    m = await bot.call("sendDocument", files={"document": (name, pdf, "application/pdf")},
                       chat_id=str(gid), message_thread_id=str(thread),
                       caption=f"📄 {d['title']} — PDF katalog", disable_notification="true",
                       **({"reply_parameters": reply} if reply else {}))
    d["pdf_msg"] = {"chat_id": gid, "message_id": m["message_id"], "sig": webapi._pdf_sig(d)}


async def refresh_pdf(design_id):
    """Jadval o'zgargach — tagidagi PDF faylni o'sha joyida yangilaydi (yangi xabar yubormaydi)."""
    import webapi
    d = store.designs.get(design_id)
    if not d or d.get("deleted") or not d.get("msg"):
        return
    pm = d.get("pdf_msg")
    sig = webapi._pdf_sig(d)
    if pm and pm.get("sig") == sig:
        return
    pdf, name = await webapi.cached_pdf(design_id)
    if pm:
        try:
            await bot.call("editMessageMedia", files={"doc": (name, pdf, "application/pdf")},
                           chat_id=str(pm["chat_id"]), message_id=str(pm["message_id"]),
                           media={"type": "document", "media": "attach://doc",
                                  "caption": f"📄 {d['title']} — PDF katalog"})
            pm["sig"] = sig
            store.save()
            return
        except TelegramError as e:
            if "not modified" in e.description:
                pm["sig"] = sig
                return
            await bot.safe_delete(pm["chat_id"], pm["message_id"])
    gid, thread = _target(d["section"])
    await _post_pdf(d, gid, thread)
    store.save()


async def ensure_pdfs(notify_uid=None):
    """Guruhdagi har bir jadval tagida PDF borligini tekshiradi; yo'q yoki eskirgan bo'lsa — joylaydi/yangilaydi.
    To'xtab qolgan ish (masalan server qayta ishga tushgani) shu bilan oxirigacha yetkaziladi."""
    todo = [d["id"] for d in store.designs.values()
            if d.get("msg") and not d.get("deleted") and store.section(d["section"])["kind"] == "design"]
    done = 0
    for did in todo:
        try:
            before = (store.designs[did].get("pdf_msg") or {}).get("sig")
            await refresh_pdf(did)
            if (store.designs[did].get("pdf_msg") or {}).get("sig") != before:
                done += 1
                await asyncio.sleep(3)
        except NotConfigured:
            return
        except Exception as e:  # noqa
            await _report(f"{store.designs[did]['title']}: PDF joylanmadi", e)
    if notify_uid and done:
        await bot.send(notify_uid, f"📄 {done} ta jadval tagiga PDF katalog joylandi/yangilandi.")


_pdf_tasks: dict = {}


def queue_pdf(design_id, delay=60):
    t = _pdf_tasks.get(design_id)
    if t and not t.done():
        t.cancel()

    async def later():
        try:
            await asyncio.sleep(delay)
            await refresh_pdf(design_id)
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa
            await _report("PDF yangilanmadi", e)
    _pdf_tasks[design_id] = asyncio.get_event_loop().create_task(later())


async def _post_new(d, kind, gid, thread):
    markup = keyboard(d) if kind == "design" else (
        {"inline_keyboard": [[{"text": "📒 Katalog", "url": catalog_url(d["id"])}]]} if public_links() else None)
    if kind == "photos":
        if not d.get("photos"):
            return
        m = await bot.call("sendPhoto", chat_id=gid, message_thread_id=thread, photo=d["photos"][0],
                           caption=esc(d.get("note") or d["title"]), parse_mode="HTML")
        d["msg"] = {"chat_id": gid, "message_id": m["message_id"], "mode": "photo", "extra_ids": []}
        return

    if _rich_allowed():
        html_text, media = design_html(d) if kind == "design" else nabor_html(d)
        try:
            m = await _send_rich(gid, thread, html_text, media, markup)
            d["msg"] = {"chat_id": gid, "message_id": m["message_id"], "mode": "rich", "extra_ids": []}
            if store.settings.get("rich_ok") is None:
                store.settings["rich_ok"] = True
            return
        except TelegramError as e:
            _note_rich_failure(e)
            await _report(f"{d['title']}: boy xabar yuborilmadi, oddiy usulga o'tildi", e)

    # zaxira usuli
    extra = []
    if kind == "design":
        if len(d["photos"]) > 1:
            album = [{"type": "photo", "media": f} for f in d["photos"][:10]]
            album[0]["caption"] = d["title"]
            ms = await bot.call("sendMediaGroup", chat_id=gid, message_thread_id=thread, media=album)
            extra = [x["message_id"] for x in ms]
        elif d["photos"]:
            ms = await bot.call("sendPhoto", chat_id=gid, message_thread_id=thread, photo=d["photos"][0],
                                caption=d["title"])
            extra = [ms["message_id"]]
        png = images.render_table(d["title"], design_table_rows(d), d.get("note", ""))
        m = await bot.call("sendPhoto", files={"photo": ("jadval.jpg", png, "image/jpeg")},
                           chat_id=str(gid), message_thread_id=str(thread), caption=d["title"],
                           reply_markup=markup)
    else:  # nabor
        if d.get("photos"):
            m = await bot.call("sendPhoto", chat_id=gid, message_thread_id=thread, photo=d["photos"][0],
                               caption=nabor_caption(d), parse_mode="HTML", reply_markup=markup)
        else:
            m = await bot.send(gid, nabor_caption(d), message_thread_id=thread, reply_markup=markup)
    d["msg"] = {"chat_id": gid, "message_id": m["message_id"], "mode": "fallback", "extra_ids": extra}


async def _update(d, kind) -> bool:
    """Mavjud postni tahrirlaydi. Muvaffaqiyatli bo'lsa True."""
    m = d["msg"]
    markup = keyboard(d) if kind == "design" else (
        {"inline_keyboard": [[{"text": "📒 Katalog", "url": catalog_url(d["id"])}]]} if public_links() else None)
    try:
        if kind == "photos":
            await bot.call("editMessageCaption", chat_id=m["chat_id"], message_id=m["message_id"],
                           caption=esc(d.get("note") or d["title"]), parse_mode="HTML")
        elif m.get("mode") == "rich":
            html_text, media = design_html(d) if kind == "design" else nabor_html(d)
            await _edit_rich(m["chat_id"], m["message_id"], html_text, media, markup)
        elif kind == "design":
            png = images.render_table(d["title"], design_table_rows(d), d.get("note", ""))
            await bot.call("editMessageMedia", files={"jadval": ("jadval.jpg", png, "image/jpeg")},
                           chat_id=str(m["chat_id"]), message_id=str(m["message_id"]),
                           media={"type": "photo", "media": "attach://jadval", "caption": d["title"]},
                           reply_markup=markup)
        else:
            if d.get("photos"):
                await bot.call("editMessageCaption", chat_id=m["chat_id"], message_id=m["message_id"],
                               caption=nabor_caption(d), parse_mode="HTML", reply_markup=markup)
            else:
                await bot.call("editMessageText", chat_id=m["chat_id"], message_id=m["message_id"],
                               text=nabor_caption(d), parse_mode="HTML", reply_markup=markup)
        return True
    except TelegramError as e:
        if "not modified" in e.description:
            return True
        await _report(f"{d['title']}: post tahrirlanmadi, qaytadan joylanadi", e)
        return False


# ---------------- ko'rsatkich (indeks) postlari: BODEN va naborlar ----------------
async def publish_index(section_key):
    if section_key != "boden" and not section_key.startswith("nabor"):
        return
    async with _lock("index:" + section_key):
        gid, thread = _target(section_key)
        info = store.s["index_msgs"].get(section_key)
        markup = {"inline_keyboard": [[{"text": "📒 Katalog", "url": catalog_url()}]]} if public_links() else None
        if section_key == "boden":
            btns = []
            for d in store.designs_in("boden"):
                link = store.post_link(d)
                if link:
                    btns.append({"text": d["title"], "url": link})
            rows = [btns[i:i + 3] for i in range(0, len(btns), 3)]
            if public_links():
                rows.append([{"text": "📒 Katalog", "url": catalog_url()}])
            markup = {"inline_keyboard": rows} if rows else None
        text_rich = index_html(section_key)
        text_plain = index_text(section_key)
        if info:
            try:
                if info.get("mode") == "rich":
                    await _edit_rich(info["chat_id"], info["message_id"], text_rich, [], markup)
                else:
                    await bot.call("editMessageText", chat_id=info["chat_id"], message_id=info["message_id"],
                                   text=text_plain, parse_mode="HTML", reply_markup=markup,
                                   link_preview_options={"is_disabled": True})
                return
            except TelegramError as e:
                if "not modified" in e.description:
                    return
                await bot.safe_delete(info["chat_id"], info["message_id"])
        if _rich_allowed():
            try:
                m = await _send_rich(gid, thread, text_rich, [], markup)
                store.s["index_msgs"][section_key] = {"chat_id": gid, "message_id": m["message_id"], "mode": "rich"}
                store.save()
                return
            except TelegramError as e:
                _note_rich_failure(e)
        m = await bot.send(gid, text_plain, message_thread_id=thread, reply_markup=markup)
        store.s["index_msgs"][section_key] = {"chat_id": gid, "message_id": m["message_id"], "mode": "text"}
        store.save()


# ---------------- kechiktirilgan (to'plab) yangilash ----------------
def queue_update(design_id: str, delay: float = 4):
    """Bir nechta o'zgarishni birlashtirib, postni bir marta yangilaydi."""
    key = "d:" + design_id
    t = _pending.get(key)
    if t and not t.done():
        t.cancel()
    _pending[key] = asyncio.get_event_loop().create_task(_later(delay, publish, design_id))
    d = store.designs.get(design_id)
    if d and d["section"] != "lenon":
        queue_index(d["section"], delay + 3)


def queue_index(section_key: str, delay: float = 6):
    if section_key == "lenon" or section_key == "rasmlar":
        return
    key = "i:" + section_key
    t = _pending.get(key)
    if t and not t.done():
        t.cancel()
    _pending[key] = asyncio.get_event_loop().create_task(_later(delay, publish_index, section_key))


async def _later(delay, fn, arg):
    try:
        await asyncio.sleep(delay)
        await fn(arg)
    except asyncio.CancelledError:
        pass
    except NotConfigured:
        pass
    except Exception as e:  # noqa
        await _report(f"Guruhga joylashda xato ({arg})", e)
