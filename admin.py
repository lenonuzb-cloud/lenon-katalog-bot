"""5-agent: siz bilan ishlaydi (menyu, rasm qabul qilish, qolmadi/keldi).
6-agent: jadvallarni tahrirlashga yordam beradi."""
import asyncio
import html
import json
import logging
import re

import checker
import config
import images
import notify
import botextra
import ops
import publisher
import report
from store import store, now
from tg import bot, TelegramError

log = logging.getLogger("admin")

sessions: dict[int, dict] = {}
_albums: dict[str, dict] = {}

ANON_BOT_ID = 1087968824  # Telegram'ning "anonim admin" boti

BTN_PHOTO = "📷 Rasm yuklash"
BTN_STOCK = "📦 Qolmadi / Keldi"
BTN_EDIT = "✏️ Tahrirlash"
BTN_ADD = "➕ Yangi mahsulot"
BTN_REPORT = "📊 Hisobot"
BTN_CHECK = "🛠 Tekshirish"
BTN_SETTINGS = "⚙️ Sozlamalar"
BTN_CATALOG = "📒 Katalog"
BTN_SEARCH = "🔍 Qidirish"


def esc(t):
    return html.escape(str(t or ""), quote=False)


def webapp_url(params: str = "") -> str:
    return f"{config.PUBLIC_URL}/app{params}" if config.PUBLIC_URL else ""


def main_menu():
    rows = [[{"text": BTN_SEARCH}],
            [{"text": BTN_PHOTO}, {"text": BTN_STOCK}],
            [{"text": BTN_EDIT}, {"text": BTN_ADD}],
            [{"text": BTN_REPORT}, {"text": BTN_CHECK}]]
    last = [{"text": BTN_SETTINGS}]
    if webapp_url():
        last.insert(0, {"text": BTN_CATALOG, "web_app": {"url": webapp_url()}})
    rows.append(last)
    return {"keyboard": rows, "resize_keyboard": True, "is_persistent": True}


def ikb(rows):
    return {"inline_keyboard": rows}


def btn(text, data):
    return {"text": text, "callback_data": data}


def grid(buttons, n=3):
    return [buttons[i:i + n] for i in range(0, len(buttons), n)]


def design_sections():
    return [s for s in store.s["sections"] if s["kind"] == "design"]


def nabor_sections():
    return [s for s in store.s["sections"] if s["kind"] == "nabor"]


def short(d):
    t = d["title"]
    return t.replace("LENON ", "").replace("BODEN ", "B-") if len(t) > 14 else t


# =====================================================================
#                          KIRISH NUQTASI
# =====================================================================
async def handle_update(u: dict):
    try:
        if "message" in u:
            await on_message(u["message"])
        elif "callback_query" in u:
            await on_callback(u["callback_query"])
        elif "my_chat_member" in u:
            await on_my_chat_member(u["my_chat_member"])
    except ops.OpError as e:
        chat = (u.get("message") or (u.get("callback_query") or {}).get("message") or {}).get("chat", {})
        if chat.get("id"):
            await bot.send(chat["id"], f"⚠️ {esc(e)}")
    except publisher.NotConfigured as e:
        chat = (u.get("message") or (u.get("callback_query") or {}).get("message") or {}).get("chat", {})
        if chat.get("id"):
            await bot.send(chat["id"], f"⚠️ {esc(e)}")
    except Exception as e:  # noqa
        await checker.report_error("Xabarni qayta ishlashda xato", e)


# =====================================================================
#                              GURUH
# =====================================================================
async def on_my_chat_member(upd):
    chat = upd["chat"]
    if chat["type"] in ("group", "supergroup"):
        status = upd["new_chat_member"]["status"]
        owner = store.settings.get("owner_id")
        if status == "administrator":
            store.settings["group_id"] = chat["id"]
            store.settings["group_username"] = chat.get("username", "") or store.settings.get("group_username", "")
            store.save()
        if owner:
            if status == "administrator":
                await bot.send(owner, f"✅ Bot «{esc(chat.get('title'))}» guruhiga admin qilib qo'shildi.\n\n"
                                      "Endi guruhning har bir bo'limida quyidagi buyruqni bir marta yozing:\n"
                                      + "\n".join(f"• {v} → <code>/bolim {k}</code>" for k, v in config.TOPIC_KEYS.items()))
            elif status in ("left", "kicked"):
                await bot.send(owner, f"⚠️ Bot «{esc(chat.get('title'))}» guruhidan chiqarildi.")


def _topic_key_by_name(name: str):
    n = re.sub(r"[^\w\s]", "", (name or "").lower()).strip()
    n = re.sub(r"\s+", " ", n)
    for k, v in config.TOPIC_KEYS.items():
        if n == v.lower():
            return k
    # yumshoq moslash: "LENON nabor 44 talik", "📦 BODEN" va h.k.
    for num in ("44", "33", "39"):
        if "nabor" in n and num in n:
            return "nabor" + num
    for k in ("muhokama", "rasmlar", "boden", "lenon"):
        if k in n or (k == "rasmlar" and "rasm" in n):
            return k
    return None


async def assign_topic(key, thread, chat):
    """Bo'limni ulaydi. Shu bo'lim boshqa kalitga xato ulangan bo'lsa — o'sha bog'lanishni olib tashlaydi.
    Bo'lim o'zgargan bo'lsa, oldin noto'g'ri joyga tushgan postlar to'g'ri bo'limga qayta joylanadi."""
    st = store.settings
    st["group_id"] = chat["id"]
    st["group_username"] = chat.get("username", "") or st.get("group_username", "")
    old = st["topics"].get(key)
    stolen = [k for k, v in st["topics"].items() if v == thread and k != key]
    for k in stolen:
        st["topics"].pop(k, None)
    st["topics"][key] = thread
    store.save()
    owner = st.get("owner_id")
    if stolen and owner:
        await bot.send(owner, "ℹ️ Bu bo'lim avval xato bilan «" + ", ".join(config.TOPIC_KEYS[k] for k in stolen)
                       + f"» deb ulangan edi — tuzatildi, endi u «{config.TOPIC_KEYS[key]}».\n"
                       + "\n".join(f"Iltimos, «{config.TOPIC_KEYS[k]}» bo'limiga kirib <code>/bolim {k}</code> deb yozing."
                                   for k in stolen))
    if old and old != thread:
        asyncio.get_event_loop().create_task(repost_section(key))
    await topic_connected(key)


async def repost_section(key):
    """Bo'limdagi bot postlarini o'chirib, to'g'ri bo'limga qaytadan joylaydi."""
    owner = store.settings.get("owner_id")
    todo = [d for d in store.designs.values() if d["section"] == key and d.get("msg") and not d.get("deleted")]
    info = store.s["index_msgs"].pop(key, None)
    if info:
        await bot.safe_delete(info["chat_id"], info["message_id"])
    if not todo and not info:
        return
    if owner:
        await bot.send(owner, f"🔁 «{config.TOPIC_KEYS[key]}»: {len(todo)} ta post to'g'ri bo'limga qayta joylanmoqda…")
    for d in todo:
        try:
            await publisher.publish(d["id"], repost=True)
        except publisher.NotConfigured:
            return
        except Exception as e:  # noqa
            await checker.report_error(f"{d['title']} qayta joylanmadi", e)
        await asyncio.sleep(3.5)
    try:
        await publisher.publish_index(key)
    except Exception:  # noqa
        pass
    if owner:
        await bot.send(owner, f"✅ «{config.TOPIC_KEYS[key]}» qayta joylandi.")


async def topic_connected(key):
    st = store.settings
    owner = st.get("owner_id")
    if not owner:
        return
    missing = [v for k, v in config.TOPIC_KEYS.items() if not st["topics"].get(k)]
    if missing:
        await bot.send(owner, f"✅ «{config.TOPIC_KEYS[key]}» bo'limi ulandi.\nQolgan bo'limlar: " + ", ".join(missing))
    else:
        await bot.send(owner, f"✅ «{config.TOPIC_KEYS[key]}» bo'limi ulandi.\n<b>Barcha bo'limlar sozlandi ✅</b>\n\n"
                              "Jadvallarni katalogdagi holati bilan (qolmadi/keldi belgilari va katalog havolasi bilan) "
                              "guruhga joylaymi?",
                       reply_markup=ikb([[btn("📤 Ha, hammasini joylash", "se:puby")], [btn("Keyinroq", "noop")]]))


async def on_group_message(m):
    text = (m.get("text") or "").strip()
    if not text.startswith("/"):
        return
    cmd, _, arg = text.partition(" ")
    cmd = cmd.split("@")[0].lower()
    user = m.get("from", {})
    is_anon_admin = m.get("sender_chat", {}).get("id") == m["chat"]["id"] or user.get("id") == ANON_BOT_ID
    if not (store.is_admin(user.get("id")) or is_anon_admin):
        return
    chat = m["chat"]
    if cmd == "/bolim":
        key = arg.strip().lower()
        if not key:
            # /bolim yolg'iz yozilsa — bo'lim nomidan o'zi aniqlaydi
            await bot.safe_delete(chat["id"], m["message_id"])
            created = (m.get("reply_to_message") or {}).get("forum_topic_created")
            if created and _topic_key_by_name(created.get("name", "")) and m.get("message_thread_id"):
                await assign_topic(_topic_key_by_name(created["name"]), m["message_thread_id"], chat)
            else:
                owner = store.settings.get("owner_id")
                if owner:
                    await bot.send(owner, "⚠️ Bu bo'limni nomidan aniqlab bo'lmadi. Bo'lim ichida masalan "
                                          "<code>/bolim lenon</code> deb yozing. Kalitlar: " + ", ".join(config.TOPIC_KEYS))
            return
        if key not in config.TOPIC_KEYS:
            await bot.send(chat["id"], "Bo'lim kalitlari: " + ", ".join(config.TOPIC_KEYS),
                           message_thread_id=m.get("message_thread_id"))
            return
        thread = m.get("message_thread_id") if m.get("is_topic_message") else None
        if not thread:
            # guruhning umumiy (General) bo'limi — Telegram unga raqam bermaydi, -1 deb saqlaymiz
            thread = -1
        await bot.safe_delete(chat["id"], m["message_id"])
        await assign_topic(key, thread, chat)
    elif cmd == "/tekshir":
        await bot.safe_delete(chat["id"], m["message_id"])


# =====================================================================
#                          SHAXSIY CHAT
# =====================================================================
async def on_message(m):
    chat = m["chat"]
    if chat["type"] in ("group", "supergroup"):
        await on_group_message(m)
        return
    if chat["type"] != "private":
        return
    user = m.get("from", {})
    uid = user["id"]
    uname = (user.get("username") or "").lower()
    if uname:
        store.s["users"][uname] = uid
    text = (m.get("text") or "").strip()

    if text.startswith("/start"):
        await cmd_start(m, user)
        return

    if not store.is_admin(uid):
        await send_public_welcome(uid)
        return

    # --- adminlar uchun ---
    if m.get("photo") or (m.get("document") and str(m["document"].get("mime_type", "")).startswith("image/")):
        await collect_photo(m)
        return
    if m.get("forward_origin") or m.get("forward_date"):
        if await try_import_forward(m):
            return

    sess = sessions.get(uid)
    if text in (BTN_SEARCH, BTN_PHOTO, BTN_STOCK, BTN_EDIT, BTN_ADD, BTN_REPORT, BTN_CHECK, BTN_SETTINGS) or text.startswith("/"):
        sessions.pop(uid, None)
        await on_menu(uid, text)
        return
    if sess and sess.get("await"):
        await on_awaited_text(uid, sess, text)
        return
    if await quick_stock_text(uid, text):
        return
    if text:
        await botextra.show_search(uid, text)
        return
    await bot.send(uid, "Menyudan tanlang 👇 yoki rasm yuboring.\n\n"
                        "Tezkor buyruq: <code>14-NL YA5724 qolmadi</code>, <code>Y936-2 keldi</code>",
                   reply_markup=main_menu())


async def cmd_start(m, user):
    uid = user["id"]
    st = store.settings
    uname = (user.get("username") or "").lower()
    payload = (m.get("text") or "").partition(" ")[2].strip()
    if not st.get("owner_id") and config.OWNER_USERNAME and uname == config.OWNER_USERNAME:
        st["owner_id"] = uid
        store.save()
        await bot.send(uid, "👋 Assalomu alaykum! Siz bot egasi sifatida ro'yxatdan o'tdingiz.\n\n"
                            "<b>Keyingi qadam:</b> botni Lenon 55 guruhiga <b>admin</b> qilib qo'shing, "
                            "so'ng har bir bo'limda bir marta yozing:\n"
                            + "\n".join(f"• {v} → <code>/bolim {k}</code>" for k, v in config.TOPIC_KEYS.items()),
                       reply_markup=main_menu())
        return
    if store.is_admin(uid):
        await bot.send(uid, "Asosiy menyu 👇", reply_markup=main_menu())
        return
    await send_public_welcome(uid)


_member_cache: dict = {}


async def is_member(uid) -> bool:
    """Adminlar va Lenon 55 guruhi a'zolari — True. Natija 10 daqiqa eslab qolinadi."""
    if store.is_admin(uid):
        return True
    gid = store.settings.get("group_id")
    if not gid:
        return False
    import time as _t
    hit = _member_cache.get(uid)
    if hit and _t.time() - hit[1] < 600:
        return hit[0]
    try:
        cm = await bot.call("getChatMember", chat_id=gid, user_id=uid)
        ok = cm.get("status") in ("creator", "administrator", "member") or (
            cm.get("status") == "restricted" and cm.get("is_member"))
    except TelegramError:
        ok = False
    _member_cache[uid] = (ok, _t.time())
    return ok


async def send_public_welcome(uid):
    if store.settings.get("private", True):
        if not await is_member(uid):
            await bot.send(uid, "🔒 Bu bot yopiq — faqat Lenon 55 guruhi a'zolari uchun.")
            return
        if webapp_url():
            try:
                await bot.call("setChatMenuButton", chat_id=uid, menu_button={
                    "type": "web_app", "text": "📒 Katalog", "web_app": {"url": webapp_url()}})
            except TelegramError:
                pass
    rows = []
    if webapp_url():
        rows.append([{"text": "📒 Katalogni ochish", "web_app": {"url": webapp_url()}}])
    await bot.send(uid, "👋 Assalomu alaykum! Bu <b>LENON</b> katalog boti.\n"
                        "Mahsulotlarni ko'rish uchun katalogni oching 👇",
                   reply_markup=ikb(rows) if rows else None)


# =====================================================================
#                              MENYU
# =====================================================================
async def on_menu(uid, text):
    cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else text
    if cmd in (BTN_SEARCH, "/qidir"):
        await botextra.ask_search(uid, sessions)
    elif cmd in (BTN_PHOTO,):
        await bot.send(uid, "📷 Rasm(lar)ni shu yerga yuboring. Bir dizayn uchun bir nechta rasmni birga (albom qilib) yuborishingiz mumkin.\n\n"
                            "Maslahat: rasm izohiga dizayn nomini yozsangiz (masalan <code>14-NL</code> yoki <code>Y878-UW4</code>), "
                            "bot uni o'zi topadi.")
    elif cmd in (BTN_STOCK, "/qolmadi", "/keldi"):
        await stock_sections(uid)
    elif cmd in (BTN_EDIT, "/tahrir"):
        await edit_start(uid)
    elif cmd in (BTN_ADD, "/qosh"):
        await add_start(uid)
    elif cmd in (BTN_REPORT, "/hisobot"):
        await bot.send(uid, "Qaysi davr uchun?", reply_markup=ikb([[btn("Bugun", "rep:24"), btn("7 kun", "rep:168"),
                                                                     btn("30 kun", "rep:720")]]))
    elif cmd in (BTN_CHECK, "/tekshir"):
        await do_check(uid)
    elif cmd in (BTN_SETTINGS, "/sozlamalar"):
        await settings_menu(uid)
    elif cmd in ("/aylantir", "/navbat", "/guruhga") and config.ROTATION:
        import rotation
        if cmd == "/navbat":
            await bot.send(uid, rotation.status_text())
        elif cmd == "/aylantir":
            try:
                t = await rotation.post_next("qo'lda")
                await bot.send(uid, f"✅ Guruhga joylandi: <b>{esc(t)}</b>" if t else "Navbat bo'sh.")
            except Exception as e:  # noqa
                await bot.send(uid, f"⚠️ Avto-post xatosi: {esc(e)}")
        else:
            q = text.partition(" ")[2].strip()
            d = store.find_design(q) if q else None
            if not d:
                await bot.send(uid, "Qaysi tovar? Masalan: <code>/guruhga SR95</code>")
            else:
                t = await rotation.post_now(d["id"])
                await bot.send(uid, f"✅ Guruhga joylandi va navbat oxiriga qo'yildi: <b>{esc(t)}</b>")
    elif cmd == "/arxiv":
        q = text.partition(" ")[2].strip()
        d = store.find_design(q) if q else None
        if not d:
            await bot.send(uid, "Qaysi dizayn? Masalan: <code>/arxiv 01-AL</code>")
        else:
            import webapi
            note = await bot.send(uid, f"⏳ «{esc(d['title'])}» rasmlari arxivlanmoqda…")
            data, name, n = await webapi.photos_zip(d["id"])
            await bot.safe_delete(uid, note["message_id"])
            if not n:
                await bot.send(uid, f"«{esc(d['title'])}» da hali rasm yo'q.")
            else:
                await bot.call("sendDocument", files={"document": (name, data, "application/zip")}, chat_id=str(uid),
                               caption=f"🗂 {d['title']}: {n} ta rasm — har biri mahsulot kodi bilan nomlangan")
    elif cmd == "/rasm":
        q = text.partition(" ")[2].strip()
        d = store.find_design(q) if q else None
        if not d:
            await bot.send(uid, "Qaysi dizayn? Masalan: <code>/rasm 01-AL</code> yoki <code>/rasm BODEN 003</code>")
        else:
            import webapi
            n = await webapi.send_design_photos(uid, d["id"])
            if not n:
                await bot.send(uid, f"«{esc(d['title'])}» da hali rasm yo'q.")
    elif cmd == "/pdf":
        q = text.partition(" ")[2].strip()
        await pdf_command(uid, q)
    elif cmd == "/rasmlar":
        if store.is_owner(uid):
            import relabel
            asyncio.get_event_loop().create_task(relabel.run(uid))
    elif cmd == "/pin":
        await pin_catalog(uid)
    elif cmd == "/joyla":
        await settings_callback(uid, "se:pub", {"id": "x"})
    elif cmd == "/zaxira":
        await bot.call("sendDocument", files={"document": ("baza.json", store.dump_bytes(), "application/json")},
                       chat_id=str(uid), caption="Katalog ma'lumotlari (zaxira nusxa)")
    else:
        await bot.send(uid, "Asosiy menyu 👇", reply_markup=main_menu())


async def do_check(uid):
    sc = await checker.self_check()
    probs = checker.audit()
    parts = ["🛠 <b>Tekshiruv natijasi</b>"]
    parts.append("<b>Bot holati:</b>\n" + ("\n".join(esc(x) for x in sc) if sc else "Hammasi joyida ✅"))
    if probs:
        shown = "\n".join(f"• {esc(p)}" for p in probs[:40])
        more = f"\n… va yana {len(probs) - 40} ta" if len(probs) > 40 else ""
        parts.append(f"<b>Jadvallardagi shubhali joylar ({len(probs)}):</b>\n{shown}{more}")
    else:
        parts.append("<b>Jadvallar:</b> xato topilmadi ✅")
    await bot.send(uid, "\n\n".join(parts)[:4000])


# =====================================================================
#                 RASM QABUL QILISH (1, 2, 3-agent ishga tushadi)
# =====================================================================
async def collect_photo(m):
    uid = m["from"]["id"]
    if m.get("photo"):
        fid = m["photo"][-1]["file_id"]
    else:
        fid = m["document"]["file_id"]
    key = m.get("media_group_id") or f"single:{m['message_id']}"
    alb = _albums.get(key)
    if not alb:
        alb = _albums[key] = {"uid": uid, "files": [], "caption": "", "task": None}
    alb["files"].append(fid)
    if m.get("caption"):
        alb["caption"] = m["caption"]
    if alb["task"]:
        alb["task"].cancel()
    alb["task"] = asyncio.get_event_loop().create_task(_album_done(key))


async def _album_done(key):
    try:
        await asyncio.sleep(1.8)
    except asyncio.CancelledError:
        return
    alb = _albums.pop(key, None)
    if not alb:
        return
    pf = (sessions.get(alb["uid"]) or {}).get("photo_for")
    if pf:
        uid = alb["uid"]
        sessions.pop(uid, None)
        try:
            data = await bot.download(alb["files"][0])
            d = store.designs[pf[0]]
            _, it = store.find_item(d, pf[1])
            _size_from_caption(it, alb.get("caption"))
            out = await asyncio.get_event_loop().run_in_executor(None, images.process, data, True,
                                                                 ops.item_label(d, it))
            r = await bot.call("sendPhoto", files={"photo": ("rasm.jpg", out, "image/jpeg")}, chat_id=str(uid),
                               caption=f"✅ {it['name']} ({d['title']}) — rasm qo'yildi")
            ops.set_item_photo(d["id"], it["id"], r["photo"][-1]["file_id"], raw=alb["files"][0])
        except Exception as e:  # noqa
            await checker.report_error("Mahsulot rasmini qo'yishda xato", e)
            await bot.send(uid, "⚠️ Rasmni qo'yib bo'lmadi, qayta urinib ko'ring.")
        return
    try:
        await process_and_ask(alb["uid"], alb["files"], alb["caption"], logo=True)
    except Exception as e:  # noqa
        await checker.report_error("Rasmni qayta ishlashda xato", e)
        await bot.send(alb["uid"], "⚠️ Rasmni qayta ishlab bo'lmadi. Yana bir bor yuborib ko'ring.")


async def process_and_ask(uid, raw_files, caption, logo=True):
    """Rasmni darhol formatlamaymiz: avval qaysi mahsulotniki ekanini so'raymiz,
    keyin logo + mahsulot yozuvi (o'lcham, nomi, kodi, narxi) bilan tayyorlaymiz."""
    sessions[uid] = {"step": "photo_dest", "raw": raw_files[:20], "photos": None, "caption": caption or "",
                     "logo": logo}
    found = store.find_design(caption) if caption else None
    rows = []
    hit = find_item_by_caption(caption) if caption else None
    if hit:
        d0, it0 = hit
        rows.append([btn(f"🖼 {short(d0)} · {it0['name']} rasmi", f"ph:it:{d0['id']}:{it0['id']}")])
    if found:
        rows.append([btn(f"➡️ «{found['title']}» ga qo'yish", f"ph:to:{found['id']}")])
    rows += [[btn("LENON", "ph:sec:lenon"), btn("BODEN", "ph:sec:boden")],
             [btn("Nabor 44", "ph:sec:nabor44"), btn("Nabor 33", "ph:sec:nabor33"), btn("Nabor 39", "ph:sec:nabor39")],
             [btn("Rasmlar bo'limi", "ph:sec:rasmlar")],
             [btn("🚫 Logosiz" if logo else "👑 Logo bilan", "ph:logo"), btn("❌ Bekor", "cancel")]]
    n = len(raw_files[:20])
    await bot.send(uid, (f"📥 {n} ta rasm qabul qilindi.\n" if n > 1 else "📥 Rasm qabul qilindi.\n")
                   + "Bu qaysi mahsulot? Avval bo'limni, keyin dizayn va mahsulotni tanlang — "
                     "rasm logo va mahsulot nomi/kodi/narxi bilan tayyorlanadi.", reply_markup=ikb(rows))


async def ensure_processed(uid, sess):
    """Dizayn slayd-shou, nabor yoki «Rasmlar» uchun: rasmlarni faqat logo bilan tayyorlaydi."""
    if sess.get("photos"):
        return
    note = await bot.send(uid, f"⏳ {len(sess['raw'])} ta rasm tayyorlanmoqda (format + logo)…")
    processed = []
    for fid in sess["raw"]:
        data = await bot.download(fid)
        processed.append(await asyncio.get_event_loop().run_in_executor(None, images.process, data, sess["logo"]))
    await bot.safe_delete(uid, note["message_id"])
    fids = []
    for i in range(0, len(processed), 10):
        chunk = processed[i:i + 10]
        if len(chunk) == 1:
            r = await bot.call("sendPhoto", files={"photo": ("rasm.jpg", chunk[0], "image/jpeg")}, chat_id=str(uid),
                               caption="Tayyor rasm ✅")
            fids.append(r["photo"][-1]["file_id"])
            continue
        files = {f"f{j}": (f"r{j}.jpg", b, "image/jpeg") for j, b in enumerate(chunk)}
        media = [{"type": "photo", "media": f"attach://f{j}"} for j in range(len(chunk))]
        media[0]["caption"] = "Tayyor rasmlar ✅"
        rs = await bot.call("sendMediaGroup", files=files, chat_id=str(uid), media=media)
        fids += [r["photo"][-1]["file_id"] for r in rs]
    sess["photos"] = fids


def _size_from_caption(it, caption):
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*sm\b", caption or "", re.I)
    if m and it is not None and not it.get("size"):
        it["size"] = m.group(1).replace(",", ".") + "sm"


def find_item_by_caption(caption: str):
    """'01-AL YA4978' yoki '01-AL Pela' -> (dizayn, mahsulot)."""
    t = caption.strip().split()
    if len(t) < 2:
        return None
    d = store.find_design(t[0])
    if not d:
        return None
    rest = " ".join(t[1:]).lower()
    for c in d["categories"]:
        for it in c["items"]:
            if it.get("code", "").lower() == rest or it["name"].lower() == rest:
                return d, it
    return None


async def try_import_forward(m) -> bool:
    """Guruhdagi eski postni botga forward qilsangiz — undagi rasmlarni katalogdagi dizaynga biriktiradi."""
    uid = m["from"]["id"]
    blob = json.dumps(m, ensure_ascii=False)
    photos = _extract_photos(m)
    if not photos:
        return False
    found = None
    text_parts = " ".join(re.findall(r'"(?:text|caption)":\s*"([^"]{1,200})"', blob))
    for d in sorted(store.designs.values(), key=lambda x: -len(x["title"])):
        if d.get("deleted"):
            continue
        short_t = d["title"].split()[-1]
        if re.search(r"(?<![\w-])" + re.escape(short_t) + r"(?![\w-])", text_parts, re.I):
            found = d
            break
    sessions[uid] = {"step": "photo_dest", "raw": photos, "photos": photos, "caption": "", "logo": False,
                     "forward": True}
    rows = []
    if found:
        rows.append([btn(f"✅ «{found['title']}» ga biriktirish", f"ph:to:{found['id']}")])
    rows += [[btn("LENON", "ph:sec:lenon"), btn("BODEN", "ph:sec:boden")],
             [btn("Nabor 44", "ph:sec:nabor44"), btn("Nabor 33", "ph:sec:nabor33"), btn("Nabor 39", "ph:sec:nabor39")],
             [btn("❌ Bekor", "cancel")]]
    await bot.send(uid, f"📥 Forward qilingan postdan {len(photos)} ta rasm topildi (logosi bor deb hisoblanadi).\n"
                        "Qaysi dizaynga biriktiray?", reply_markup=ikb(rows))
    return True


def _extract_photos(obj) -> list:
    """Xabar ichidagi barcha rasmlarni (eng katta o'lchamini) topadi."""
    out = []

    def walk(x):
        if isinstance(x, list):
            if x and all(isinstance(i, dict) and "file_id" in i and "width" in i for i in x):
                best = max(x, key=lambda i: i.get("width", 0) * i.get("height", 0))
                if best["file_id"] not in out:
                    out.append(best["file_id"])
                return
            for i in x:
                walk(i)
        elif isinstance(x, dict):
            for k, v in x.items():
                if k in ("thumbnail", "thumb", "from", "chat", "sender_chat"):
                    continue
                walk(v)

    walk(obj)
    return out


async def photo_callback(uid, data, cb):
    sess = sessions.get(uid)
    if not sess or sess.get("step") not in ("photo_dest", "photo_design"):
        await bot.answer_cb(cb["id"], "Sessiya eskirgan. Rasmni qaytadan yuboring.", True)
        return
    parts = data.split(":")
    if parts[1] == "logo":
        await bot.answer_cb(cb["id"])
        sess["logo"] = not sess["logo"]
        await bot.send(uid, "👑 Logo qo'yiladi." if sess["logo"] else "🚫 Logo qo'yilmaydi.")
        return
    if parts[1] == "sec":
        key = parts[2]
        sess["section"] = key
        kind = store.section(key)["kind"]
        await bot.answer_cb(cb["id"])
        if kind == "design":
            ds = store.designs_in(key)
            rows = [[btn("🆕 Yangi dizayn", "ph:new")]]
            rows += grid([btn(short(d), f"ph:to:{d['id']}") for d in ds], 3)
            rows.append([btn("❌ Bekor", "cancel")])
            await bot.send(uid, f"<b>{esc(store.section(key)['title'])}</b>: yangi dizaynmi yoki mavjud dizaynga?",
                           reply_markup=ikb(rows))
        elif kind == "nabor":
            sess["await"] = "nabor_code"
            ds = store.designs_in(key)
            hint = ", ".join(d["title"] for d in ds[:6])
            await bot.send(uid, f"Nabor kodini yozing (masalan <code>Y950-2</code>).\n"
                                f"Mavjud kod yozsangiz, o'sha naborning rasmi almashtiriladi.\n\n<i>Mavjudlar: {esc(hint)}…</i>")
        else:
            if sess.get("caption"):
                await create_rasm(uid, sess, sess["caption"])
            else:
                sess["await"] = "rasm_caption"
                await bot.send(uid, "Rasm izohini yozing (nomi va o'lchami), masalan:\n<code>Tar kv 6.5 — 16sm</code>")
        return
    if parts[1] == "new":
        await bot.answer_cb(cb["id"])
        if sess.get("raw"):
            await ensure_processed(uid, sess)
        sess["await"] = "design_title"
        ex = "15-OL" if sess.get("section") == "lenon" else "008"
        await bot.send(uid, f"Yangi dizayn nomini yozing (masalan <code>{ex}</code>):")
        return
    if parts[1] in ("to", "dz"):
        d = store.designs.get(parts[2])
        if not d:
            await bot.answer_cb(cb["id"], "Topilmadi", True)
            return
        await bot.answer_cb(cb["id"])
        sess["design"] = d["id"]
        if parts[1] == "to" and store.section(d["section"])["kind"] == "design":
            sess["idx"] = 0
            await ask_item_for_photo(uid, sess, d, 0)
            return
        if d.get("photos"):
            await bot.send(uid, f"«{esc(d['title'])}» da allaqachon {len(d['photos'])} ta rasm bor.",
                           reply_markup=ikb([[btn("🔄 Almashtirish", "ph:apply:r"), btn("➕ Qo'shish", "ph:apply:a")],
                                             [btn("❌ Bekor", "cancel")]]))
        else:
            await apply_photos(uid, sess, replace=True)
        return
    if parts[1] == "apply":
        await bot.answer_cb(cb["id"])
        await apply_photos(uid, sess, replace=parts[2] == "r")
        return
    if parts[1] == "prod":
        await bot.answer_cb(cb["id"])
        rows = [[btn(s["title"], f"ph:psec:{s['key']}") for s in design_sections()], [btn("❌ Bekor", "cancel")]]
        await bot.send(uid, "🖼 Qaysi bo'lim mahsuloti?", reply_markup=ikb(rows))
        return
    if parts[1] == "psec":
        await bot.answer_cb(cb["id"])
        rows = grid([btn(short(d), f"ph:pd:{d['id']}:0") for d in store.designs_in(parts[2])], 3)
        rows.append([btn("❌ Bekor", "cancel")])
        await bot.send(uid, "Qaysi dizayn?", reply_markup=ikb(rows))
        return
    if parts[1] == "pg":
        await bot.answer_cb(cb["id"])
        d = store.designs[parts[2]]
        try:
            await bot.call("editMessageReplyMarkup", chat_id=uid, message_id=cb["message"]["message_id"],
                           reply_markup=item_pick_markup(d, int(parts[3])))
        except TelegramError:
            pass
        return
    if parts[1] == "pd":
        await bot.answer_cb(cb["id"])
        d = store.designs[parts[2]]
        sess["design"] = d["id"]
        sess.setdefault("idx", 0)
        await ask_item_for_photo(uid, sess, d, int(parts[3]))
        return
    if parts[1] == "it":
        await bot.answer_cb(cb["id"])
        d = store.designs[parts[2]]
        _, it = store.find_item(d, parts[3])
        if not it:
            return
        i = sess.get("idx", 0)
        await bot.safe_delete(uid, cb["message"]["message_id"])
        raw = None
        if sess.get("photos"):          # forward qilingan, allaqachon tayyor rasm
            fid = sess["photos"][i]
        else:
            raw = sess["raw"][i]
            _size_from_caption(it, sess.get("caption"))
            data = await bot.download(sess["raw"][i])
            out = await asyncio.get_event_loop().run_in_executor(None, images.process, data, sess["logo"],
                                                                 ops.item_label(d, it))
            r = await bot.call("sendPhoto", files={"photo": ("rasm.jpg", out, "image/jpeg")}, chat_id=str(uid),
                               caption=f"✅ {it['name']} ({d['title']}) — rasm qo'yildi")
            fid = r["photo"][-1]["file_id"]
        ops.set_item_photo(d["id"], it["id"], fid, raw=raw)
        sess["idx"] = i + 1
        if sess["idx"] < len(sess["raw"]):
            await ask_item_for_photo(uid, sess, d, 0)
        else:
            n = len(sess["raw"])
            sessions.pop(uid, None)
            await bot.send(uid, f"✅ Tayyor: {n} ta mahsulot rasmi katalogga qo'yildi.", reply_markup=main_menu())
        return
    if parts[1] == "skip":
        await bot.answer_cb(cb["id"])
        d = store.designs[sess["design"]]
        await bot.safe_delete(uid, cb["message"]["message_id"])
        sess["idx"] = sess.get("idx", 0) + 1
        if sess["idx"] < len(sess["raw"]):
            await ask_item_for_photo(uid, sess, d, 0)
        else:
            sessions.pop(uid, None)
            await bot.send(uid, "Tayyor ✅", reply_markup=main_menu())


def item_pick_markup(d, page):
    items = [it for c in d["categories"] for it in c["items"]]
    chunk = items[page * PAGE:(page + 1) * PAGE]
    rows = [[btn("🖼 Dizaynning umumiy rasmi (slayd-shou)", f"ph:dz:{d['id']}")]]
    rows += grid([btn(("✅ " if it.get("photo") else "") + it["name"], f"ph:it:{d['id']}:{it['id']}") for it in chunk], 2)
    nav = []
    if page > 0:
        nav.append(btn("⬅️ Oldingi", f"ph:pg:{d['id']}:{page - 1}"))
    if (page + 1) * PAGE < len(items):
        nav.append(btn("Keyingi ➡️", f"ph:pg:{d['id']}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([btn("⏭ O'tkazib yuborish", "ph:skip"), btn("❌ Bekor", "cancel")])
    return ikb(rows)


async def ask_item_for_photo(uid, sess, d, page):
    i = sess.get("idx", 0)
    n = len(sess["raw"])
    head = f"{i + 1}/{n}-rasm. " if n > 1 else ""
    await bot.call("sendPhoto", chat_id=uid, photo=(sess.get("photos") or sess["raw"])[i],
                   caption=f"{head}{d['title']}: bu qaysi mahsulot? (✅ — rasmi bor)",
                   reply_markup=item_pick_markup(d, page))


async def apply_photos(uid, sess, replace):
    await ensure_processed(uid, sess)
    d = store.designs[sess["design"]]
    ops.mark_design_photos(d["id"], sess["photos"], replace)
    sessions.pop(uid, None)
    rep = bool(d.get("msg")) and d["msg"].get("mode") != "rich"
    try:
        await publisher.publish(d["id"], repost=rep)
        await bot.send(uid, f"✅ «{esc(d['title'])}» rasmlari yangilandi va guruhdagi post yangilandi.",
                       reply_markup=main_menu())
    except publisher.NotConfigured as e:
        await bot.send(uid, f"✅ Rasmlar katalogga saqlandi.\n⚠️ {esc(e)}", reply_markup=main_menu())


async def create_rasm(uid, sess, caption):
    await ensure_processed(uid, sess)
    did = store.add_design("rasmlar", caption.split("\n")[0][:60], note=caption, photos=sess["photos"][:1])
    for extra in sess["photos"][1:]:
        store.add_design("rasmlar", caption.split("\n")[0][:60], note=caption, photos=[extra], log_event=False)
    store.save()
    sessions.pop(uid, None)
    try:
        for d in [x for x in store.designs_in("rasmlar") if not x.get("msg")]:
            await publisher.publish(d["id"])
        await bot.send(uid, "✅ Rasm «Rasmlar» bo'limiga joylandi.", reply_markup=main_menu())
    except publisher.NotConfigured as e:
        await bot.send(uid, f"✅ Saqlandi. ⚠️ {esc(e)}", reply_markup=main_menu())


# =====================================================================
#                 YANGI DIZAYN YARATISH (jadvali bilan)
# =====================================================================
TABLE_HELP = ("Jadvalni shu ko'rinishda yozing — har bir qator alohida:\n\n"
              "<code>#Tarelka bort\nTarelka 8 | KG0014 | 4$\nTarelka 9 | KG0015 | 5.2$\n#Aval\nAval 12 | YA5576 | 8$</code>\n\n"
              "<b>#</b> bilan boshlangan qator — guruh nomi. BODEN uchun kod shart emas: <code>Tarelka 8 | 2.3$</code>")


def parse_table(text: str) -> tuple[list, list]:
    cats, warns = [], []
    cur = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            cur = {"name": line.lstrip("#").strip(), "items": []}
            cats.append(cur)
            continue
        parts = [p.strip() for p in re.split(r"\s*[|;]\s*|\t+", line) if p.strip()]
        if len(parts) == 2:
            name, code, price = parts[0], "", parts[1]
        elif len(parts) >= 3:
            name, code, price = parts[0], parts[1], parts[2]
        else:
            warns.append(f"tushunilmadi: «{line}»")
            continue
        if cur is None:
            cur = {"name": "Asosiy", "items": []}
            cats.append(cur)
        price = checker.normalize_price(price)
        code = checker.normalize_code(code)
        for w in checker.check_item(name, code, price, bool(code)):
            warns.append(f"{name}: {w}")
        cur["items"].append({"name": name, "code": code, "price": price})
    return [c for c in cats if c["items"]], warns


async def design_table_choice(uid, sess):
    rows = [[btn("📋 Boshqa dizayndan nusxa", "nd:copy")],
            [btn("✍️ O'zim yozaman", "nd:write")],
            [btn("⏭ Jadvalsiz (keyin qo'shaman)", "nd:empty")],
            [btn("❌ Bekor", "cancel")]]
    await bot.send(uid, f"<b>{esc(sess['title'])}</b> jadvali qanday bo'ladi?", reply_markup=ikb(rows))


async def new_design_callback(uid, data, cb):
    sess = sessions.get(uid)
    if not sess or "title" not in sess:
        await bot.answer_cb(cb["id"], "Sessiya eskirgan", True)
        return
    await bot.answer_cb(cb["id"])
    act = data.split(":")[1]
    if act == "copy":
        ds = store.designs_in(sess["section"])
        rows = grid([btn(short(d), f"nd:from:{d['id']}") for d in ds], 3)
        rows.append([btn("❌ Bekor", "cancel")])
        await bot.send(uid, "Qaysi dizayn jadvalidan nusxa olay?", reply_markup=ikb(rows))
    elif act == "from":
        src = store.designs[data.split(":")[2]]
        sess["categories"] = [{"name": c["name"], "items": [{"name": i["name"], "code": i.get("code", ""),
                                                              "price": i["price"]} for i in c["items"]]}
                              for c in src["categories"]]
        await design_preview(uid, sess, [])
    elif act == "write":
        sess["await"] = "design_table"
        await bot.send(uid, TABLE_HELP)
    elif act == "empty":
        sess["categories"] = []
        await design_preview(uid, sess, [])
    elif act == "go":
        await design_create(uid, sess)


async def design_preview(uid, sess, warns):
    n = sum(len(c["items"]) for c in sess["categories"])
    lines = [f"<b>{esc(sess['title'])}</b>",
             f"Rasmlar: {len(sess.get('photos') or [])} ta",
             f"Guruhlar: {len(sess['categories'])} ta, mahsulotlar: {n} ta"]
    for c in sess["categories"][:12]:
        lines.append(f"• {esc(c['name'])}: {len(c['items'])} ta")
    if warns:
        lines.append("\n⚠️ <b>Nazoratchi eslatmalari:</b>\n" + "\n".join(f"– {esc(w)}" for w in warns[:15]))
    sess["await"] = None
    await bot.send(uid, "\n".join(lines), reply_markup=ikb([[btn("✅ Guruhga joylash", "nd:go")],
                                                            [btn("❌ Bekor", "cancel")]]))


async def design_create(uid, sess):
    did = store.add_design(sess["section"], sess["title"], photos=sess.get("photos") or [])
    d = store.designs[did]
    for c in sess.get("categories", []):
        d["categories"].append({"name": c["name"], "items": [
            store._mk_item(i["name"], i.get("code", ""), i["price"], new=True) for i in c["items"]]})
    store.save()
    sessions.pop(uid, None)
    notify.add("new", f"{d['title']} — yangi dizayn")
    try:
        await publisher.publish(did)
        await bot.send(uid, f"✅ <b>{esc(d['title'])}</b> katalogga qo'shildi va guruhga joylandi. "
                            f"🆕 belgisi {config.NEW_DAYS} kun turadi.", reply_markup=main_menu())
    except publisher.NotConfigured as e:
        await bot.send(uid, f"✅ Katalogga qo'shildi.\n⚠️ {esc(e)}", reply_markup=main_menu())


# =====================================================================
#                      MATN KUTILGAN HOLATLAR
# =====================================================================
async def on_awaited_text(uid, sess, text):
    w = sess.get("await")
    if w == "search":
        sessions.pop(uid, None)
        await botextra.show_search(uid, text)
        return
    if w == "pick_q":
        sess["q"] = text.strip()
        sess["page"] = 0
        await botextra.redraw_pick(uid, sess, new=True)
        return
    if w == "reorder":
        await botextra.reorder_text(uid, sess, text, sessions)
        return
    if w == "prod_remove":
        import registry
        hits = []
        for s_ in design_sections():
            for e in registry.products(s_["key"]):
                sc = botextra.match_q(text, e["name"], e.get("code", ""))
                if sc:
                    hits.append((sc, dict(e, sec=s_["key"])))
        hits = [e for _, e in sorted(hits, key=lambda x: -x[0])[:12]]
        if not hits:
            await bot.send(uid, "Topilmadi. Boshqacha yozib ko'ring yoki ❌ Bekor qiling.",
                           reply_markup=ikb([[btn("❌ Bekor", "cancel")]]))
            return
        sess["px_hits"] = hits
        sess["await"] = None
        rows = [[btn(f"{e['name']} · {e['code'] or '—'} · {store.section(e['sec'])['title']} ({e['designs']})",
                     f"ed:pxs:{i}")] for i, e in enumerate(hits)]
        rows.append([btn("❌ Bekor", "cancel")])
        await bot.send(uid, "Qaysi biri? (qavsda — nechta dizaynda borligi)", reply_markup=ikb(rows))
        return
    if w == "design_title":
        title = text.strip()
        exists = store.find_design(title, sess["section"])
        if exists:
            await bot.send(uid, f"«{esc(exists['title'])}» allaqachon bor. Boshqa nom yozing yoki ❌ Bekor qiling.")
            return
        sess["title"] = title if sess["section"] not in ("lenon", "boden") else (
            ("LENON " if sess["section"] == "lenon" else "BODEN ") + re.sub(r"^(lenon|boden)\s*", "", title, flags=re.I))
        sess["await"] = None
        await design_table_choice(uid, sess)
    elif w == "design_table":
        cats, warns = parse_table(text)
        if not cats:
            await bot.send(uid, "Jadval tushunilmadi.\n\n" + TABLE_HELP)
            return
        sess["categories"] = cats
        await design_preview(uid, sess, warns)
    elif w == "nabor_code":
        code = text.strip().upper().replace(" ", "")
        d = store.find_design(code, sess["section"])
        if d:
            sess["design"] = d["id"]
            sess["await"] = None
            await apply_photos(uid, sess, replace=True)
            return
        await ensure_processed(uid, sess)
        did = store.add_design(sess["section"], code, photos=sess["photos"])
        store.save()
        sessions.pop(uid, None)
        notify.add("new", f"Nabor {code} ({store.section(sess['section'])['title']})")
        try:
            await publisher.publish(did)
            await bot.send(uid, f"✅ Nabor <b>{esc(code)}</b> qo'shildi va guruhga joylandi.", reply_markup=main_menu())
        except publisher.NotConfigured as e:
            await bot.send(uid, f"✅ Saqlandi. ⚠️ {esc(e)}", reply_markup=main_menu())
    elif w == "rasm_caption":
        await create_rasm(uid, sess, text)
    elif w == "add_items":
        await add_items_text(uid, sess, text)
    elif w == "edit_field":
        await edit_field_text(uid, sess, text)
    elif w == "rename":
        ops.rename_design(sess["design"], text)
        sessions.pop(uid, None)
        await bot.send(uid, "✅ Nomi o'zgartirildi.", reply_markup=main_menu())
    elif w == "admin_add":
        await admin_add_text(uid, text)
    elif w == "agent_new":
        mm = re.match(r"^\s*(\S+)\s+@?(\w{4,})\s*$", text)
        if not mm:
            await bot.send(uid, "Ko'rinishi: <code>Ism @username</code>")
            return
        store.settings["agents"][mm.group(1)] = mm.group(2)
        store.save()
        sessions.pop(uid, None)
        await bot.send(uid, f"✅ Agent qo'shildi: {esc(mm.group(1))} → @{esc(mm.group(2))}\n"
                            "Uni dizaynlarga «Tahrirlash → Agent tugmalari» orqali qo'shasiz.", reply_markup=main_menu())
    elif w == "agent_user":
        name = sess["agent"]
        store.settings["agents"][name] = text.strip().lstrip("@")
        store.save()
        sessions.pop(uid, None)
        for d in store.designs.values():
            if name in d.get("agents", []) and d.get("msg"):
                publisher.queue_update(d["id"], delay=2)
        await bot.send(uid, f"✅ {esc(name)} → @{esc(text.strip().lstrip('@'))}", reply_markup=main_menu())
    else:
        sessions.pop(uid, None)
        await bot.send(uid, "Asosiy menyu 👇", reply_markup=main_menu())


# =====================================================================
#                 QOLMADI / KELDI (5-agent asosiy ishi)
# =====================================================================
async def stock_sections(uid):
    rows = [[btn(s["title"], f"sk:sec:{s['key']}") for s in design_sections()],
            [btn(s["title"].replace("LENON ", ""), f"sk:sec:{s['key']}") for s in nabor_sections()]]
    await bot.send(uid, "📦 Qaysi bo'lim?", reply_markup=ikb(rows))


PAGE = 30


def _stock_view(sess):
    d = store.designs[sess["design"]]
    pend = sess["pending"]
    items = [(c["name"], it) for c in d["categories"] for it in c["items"]]
    p = sess.get("page", 0)
    chunk = items[p * PAGE:(p + 1) * PAGE]
    rows = []
    last_cat = None
    for cat, it in chunk:
        if cat != last_cat and cat != "Asosiy":
            rows.append([btn(f"— {cat} —", "noop")])
            last_cat = cat
        st = pend.get(it["id"], it.get("in_stock", True))
        mark = "✅" if st else "❌"
        rows.append([btn(f"{mark} {it['name']}" + (f" · {it['code']}" if it.get("code") else ""),
                         f"sk:t:{it['id']}")])
    nav = []
    if p > 0:
        nav.append(btn("⬅️", "sk:pg:-1"))
    if (p + 1) * PAGE < len(items):
        nav.append(btn("➡️", "sk:pg:1"))
    if nav:
        rows.append(nav)
    rows.append([btn(f"💾 Saqlash ({len(pend)})", "sk:save"), btn("❌ Bekor", "cancel")])
    text = (f"<b>{esc(d['title'])}</b>\nBosib belgilang: ✅ bor · ❌ qolmadi. So'ng «Saqlash».\n"
            f"Sahifa {p + 1}/{max(1, (len(items) + PAGE - 1) // PAGE)}")
    return text, ikb(rows)


def _nabor_view(sess):
    pend = sess["pending"]
    rows = []
    for d in store.designs_in(sess["section"]):
        st = pend.get(d["id"], d.get("in_stock", True))
        rows.append(btn(("✅ " if st else "❌ ") + d["title"], f"sk:n:{d['id']}"))
    rows = grid(rows, 2)
    rows.append([btn(f"💾 Saqlash ({len(pend)})", "sk:save"), btn("❌ Bekor", "cancel")])
    return f"<b>{esc(store.section(sess['section'])['title'])}</b>\nBosib belgilang: ✅ bor · ❌ qolmadi", ikb(rows)


async def stock_callback(uid, data, cb):
    parts = data.split(":")
    act = parts[1]
    msg = cb["message"]
    if act == "sec":
        key = parts[2]
        await bot.answer_cb(cb["id"])
        if store.section(key)["kind"] == "nabor":
            sessions[uid] = {"step": "stock", "section": key, "pending": {}}
            text, kb = _nabor_view(sessions[uid])
            await bot.send(uid, text, reply_markup=kb)
        else:
            ds = store.designs_in(key)
            rows = grid([btn(short(d), f"sk:d:{d['id']}") for d in ds], 3)
            await bot.send(uid, "Qaysi dizayn?", reply_markup=ikb(rows))
        return
    if act == "d":
        sessions[uid] = {"step": "stock", "design": parts[2], "pending": {}, "page": 0}
        await bot.answer_cb(cb["id"])
        text, kb = _stock_view(sessions[uid])
        await bot.send(uid, text, reply_markup=kb)
        return
    sess = sessions.get(uid)
    if not sess or sess.get("step") != "stock":
        await bot.answer_cb(cb["id"], "Sessiya eskirgan, qaytadan boshlang.", True)
        return
    if act == "t":
        d = store.designs[sess["design"]]
        _, it = store.find_item(d, parts[2])
        if it:
            cur = sess["pending"].get(it["id"], it.get("in_stock", True))
            new = not cur
            if new == it.get("in_stock", True):
                sess["pending"].pop(it["id"], None)
            else:
                sess["pending"][it["id"]] = new
        await bot.answer_cb(cb["id"])
        text, kb = _stock_view(sess)
    elif act == "n":
        d = store.designs[parts[2]]
        cur = sess["pending"].get(d["id"], d.get("in_stock", True))
        new = not cur
        if new == d.get("in_stock", True):
            sess["pending"].pop(d["id"], None)
        else:
            sess["pending"][d["id"]] = new
        await bot.answer_cb(cb["id"])
        text, kb = _nabor_view(sess)
    elif act == "pg":
        sess["page"] = max(0, sess.get("page", 0) + int(parts[2]))
        await bot.answer_cb(cb["id"])
        text, kb = _stock_view(sess)
    elif act == "save":
        n = 0
        if "design" in sess:
            for iid, val in sess["pending"].items():
                if ops.set_stock(sess["design"], iid, val):
                    n += 1
        else:
            for did, val in sess["pending"].items():
                if ops.set_stock(did, None, val):
                    n += 1
        sessions.pop(uid, None)
        await bot.answer_cb(cb["id"], f"Saqlandi: {n} ta o'zgarish")
        try:
            await bot.call("editMessageText", chat_id=uid, message_id=msg["message_id"],
                           text=f"✅ Saqlandi: {n} ta o'zgarish. Jadval bir necha soniyada yangilanadi, "
                                f"guruh a'zolariga xabar boradi.", parse_mode="HTML")
        except TelegramError:
            pass
        return
    else:
        await bot.answer_cb(cb["id"])
        return
    try:
        await bot.call("editMessageText", chat_id=uid, message_id=msg["message_id"], text=text,
                       parse_mode="HTML", reply_markup=kb)
    except TelegramError:
        pass


async def quick_stock_text(uid, text) -> bool:
    """'14-NL YA5724 qolmadi', 'Y936-2 keldi', 'YA5724 qolmadi' kabi tezkor buyruqlar."""
    m = re.match(r"^(.+?)\s+(qolmadi|tugadi|yo'q|yoq|keldi|bor)\s*$", text.strip(), re.I)
    if not m:
        return False
    target, word = m.group(1).strip(), m.group(2).lower()
    in_stock = word in ("keldi", "bor")
    sets = [x for x in store.designs.values() if not x.get("deleted")
            and store.section(x["section"])["kind"] == "nabor" and x["title"].upper() == target.upper()]
    if len(sets) == 1:
        ch = ops.set_stock(sets[0]["id"], None, in_stock)
        await bot.send(uid, ("✅ " if ch else "ℹ️ O'zgarmadi: ") + f"{esc(sets[0]['title'])} — {'bor' if in_stock else 'qolmadi'}")
        return True
    if len(sets) > 1:
        sessions[uid] = {"step": "quick", "matches": [(x["id"], None) for x in sets], "in_stock": in_stock}
        rows = [[btn(f"Hammasida ({len(sets)} ta)", "qs:all")]]
        rows += grid([btn(store.section(x["section"])["title"].replace("LENON ", ""), f"qs:{i}") for i, x in enumerate(sets)], 3)
        rows.append([btn("❌ Bekor", "cancel")])
        await bot.send(uid, f"«{esc(target)}» bir nechta naborda bor. Qaysi birida {'keldi' if in_stock else 'qolmadi'}?",
                       reply_markup=ikb(rows))
        return True
    tokens = target.split()
    design = None
    if len(tokens) >= 2:
        design = store.find_design(tokens[0])
        if design:
            target = " ".join(tokens[1:])
    matches = []
    for dd in ([design] if design else store.designs.values()):
        if dd.get("deleted"):
            continue
        for c in dd["categories"]:
            for it in c["items"]:
                if it.get("code", "").upper() == target.upper() or it["name"].lower() == target.lower():
                    matches.append((dd, it))
    if not matches:
        # aqlli qidiruv: "za 5 qolmadi" -> Zaftrik 5
        scored = []
        for dd in ([design] if design else store.designs.values()):
            if dd.get("deleted"):
                continue
            for c in dd["categories"]:
                for it in c["items"]:
                    sc = botextra.match_q(target, it["name"], it.get("code", ""))
                    if sc:
                        scored.append((sc, dd, it))
        if scored:
            best = max(x[0] for x in scored)
            names = {x[2]["name"].lower() for x in scored if x[0] == best}
            if len(names) == 1:
                matches = [(dd, it) for sc, dd, it in scored if sc == best]
            else:
                await bot.send(uid, f"«{esc(target)}» bir nechta mahsulotga to'g'ri keladi: "
                                    f"{esc(', '.join(sorted(names))[:300])}.\nAniqroq yozing.")
                return True
    if not matches:
        await bot.send(uid, f"«{esc(target)}» topilmadi. Kod yoki nomni tekshiring.")
        return True
    if len(matches) == 1:
        dd, it = matches[0]
        ch = ops.set_stock(dd["id"], it["id"], in_stock)
        await bot.send(uid, ("✅ " if ch else "ℹ️ O'zgarmadi: ") + f"{esc(it['name'])} ({esc(dd['title'])}) — "
                            f"{'bor' if in_stock else 'qolmadi'}")
        return True
    sessions[uid] = {"step": "quick", "matches": [(dd["id"], it["id"]) for dd, it in matches], "in_stock": in_stock}
    rows = grid([btn(short(dd), f"qs:{i}") for i, (dd, it) in enumerate(matches)], 3)
    rows.insert(0, [btn(f"Hammasida ({len(matches)} ta)", "qs:all")])
    rows.append([btn("❌ Bekor", "cancel")])
    target = matches[0][1]["name"]
    await bot.send(uid, f"«{esc(target)}» {len(matches)} ta dizaynda bor. Qaysi birida "
                        f"{'keldi' if in_stock else 'qolmadi'}?", reply_markup=ikb(rows))
    return True


async def quick_callback(uid, data, cb):
    sess = sessions.get(uid)
    if not sess or sess.get("step") != "quick":
        await bot.answer_cb(cb["id"], "Sessiya eskirgan", True)
        return
    which = data.split(":")[1]
    pairs = sess["matches"] if which == "all" else [sess["matches"][int(which)]]
    n = sum(1 for did, iid in pairs if ops.set_stock(did, iid, sess["in_stock"]))
    if which == "all":
        sessions.pop(uid, None)
    await bot.answer_cb(cb["id"], f"✅ {n} ta o'zgarish")


# =====================================================================
#                   6-agent: TAHRIRLASH
# =====================================================================
async def edit_start(uid):
    rows = []
    if webapp_url():
        rows.append([{"text": "📒 Katalogda tahrirlash (qulay)", "web_app": {"url": webapp_url("?admin=1")}}])
    rows.append([btn(s["title"], f"ed:sec:{s['key']}") for s in design_sections()])
    rows.append([btn(s["title"].replace("LENON ", ""), f"ed:sec:{s['key']}") for s in nabor_sections()])
    rows.append([btn("🗑 Mahsulotni butunlay o'chirish", "ed:px")])
    await bot.send(uid, "✏️ <b>Tahrirlash</b>\nKatalog ilovasida mahsulotni bosib, narx/nom/kodni o'zgartirish eng qulay. "
                        "Yoki shu yerda bo'limni tanlang:", reply_markup=ikb(rows))


async def edit_callback(uid, data, cb):
    p = data.split(":")
    act = p[1]
    await bot.answer_cb(cb["id"])
    if act == "px":       # mahsulotni butunlay o'chirish — qidirish
        sessions[uid] = {"await": "prod_remove"}
        await bot.send(uid, "🗑 Qaysi mahsulotni butunlay o'chiramiz? Nomini yoki kodini yozing "
                            "(masalan <code>ta to 9</code> yoki <code>YA4961</code>):")
        return
    if act == "pxs":      # tanlandi — qanday o'chirish
        e = (sessions.get(uid) or {}).get("px_hits", [])[int(p[2])]
        await bot.send(uid, f"🗑 <b>{esc(e['name'])}</b> · {esc(e['code'] or '—')}\n"
                            f"{esc(store.section(e['sec'])['title'])} bo'limida <b>{e['designs']} ta dizayn</b> jadvalida bor.",
                       reply_markup=ikb([[btn(f"🗑 Hamma jadvallardan o'chirish ({e['designs']})", f"ed:pxa:{p[2]}")],
                                         [btn("📚 Faqat ro'yxatdan olib tashlash", f"ed:pxh:{p[2]}")],
                                         [btn("❌ Bekor", "cancel")]]))
        return
    if act == "pxa":      # tasdiqlash
        e = (sessions.get(uid) or {}).get("px_hits", [])[int(p[2])]
        await bot.send(uid, f"«{esc(e['name'])}» {e['designs']} ta dizayn jadvalidan butunlay o'chirilsinmi? "
                            "Buni qaytarib bo'lmaydi.",
                       reply_markup=ikb([[btn("Ha, o'chirish", f"ed:pxy:{p[2]}"), btn("Yo'q", "cancel")]]))
        return
    if act in ("pxy", "pxh"):
        sess = sessions.pop(uid, None) or {}
        hits = sess.get("px_hits", [])
        if int(p[2]) >= len(hits):
            await bot.send(uid, "Sessiya eskirgan, qaytadan boshlang.")
            return
        e = hits[int(p[2])]
        n = ops.remove_product(e["sec"], e["name"], e["code"], "hide" if act == "pxh" else "all")
        await bot.send(uid, (f"✅ «{esc(e['name'])}» {n} ta dizayn jadvalidan o'chirildi. Guruhdagi jadvallar yangilanadi."
                             if act == "pxy" else f"✅ «{esc(e['name'])}» mavjud mahsulotlar ro'yxatidan olib tashlandi."),
                       reply_markup=main_menu())
        return
    if act == "sec":
        ds = store.designs_in(p[2])
        rows = grid([btn(short(d), f"ed:d:{d['id']}") for d in ds], 3)
        await bot.send(uid, "Qaysi dizayn/nabor?", reply_markup=ikb(rows))
    elif act == "d":
        d = store.designs[p[2]]
        kind = store.section(d["section"])["kind"]
        rows = []
        if kind == "design":
            rows.append([btn("📝 Mahsulotni tahrirlash", f"ed:items:{d['id']}:0")])
            rows.append([btn("➕ Mahsulot qo'shish", f"ad:d:{d['id']}")])
            rows.append([btn("👥 Agent tugmalari", f"ed:ag:{d['id']}")])
        if kind == "design":
            rows.append([btn("↕️ Mahsulotlar tartibi", f"ed:ro:{d['id']}")])
        rows.append([btn("🔤 Nomini o'zgartirish", f"ed:rn:{d['id']}"), btn("📤 Qayta joylash", f"ed:rp:{d['id']}")])
        rows.append([btn("🗑 O'chirish", f"ed:del:{d['id']}")])
        await bot.send(uid, f"<b>{esc(d['title'])}</b> — nima qilamiz?", reply_markup=ikb(rows))
    elif act == "items":
        d = store.designs[p[2]]
        page = int(p[3])
        items = [it for c in d["categories"] for it in c["items"]]
        chunk = items[page * PAGE:(page + 1) * PAGE]
        rows = [[btn(f"{it['name']} · {it.get('code', '')} · {it['price']}", f"ed:it:{d['id']}:{it['id']}")]
                for it in chunk]
        nav = []
        if page > 0:
            nav.append(btn("⬅️", f"ed:items:{d['id']}:{page - 1}"))
        if (page + 1) * PAGE < len(items):
            nav.append(btn("➡️", f"ed:items:{d['id']}:{page + 1}"))
        if nav:
            rows.append(nav)
        await bot.send(uid, f"<b>{esc(d['title'])}</b>: qaysi mahsulot?", reply_markup=ikb(rows))
    elif act == "it":
        d = store.designs[p[2]]
        c, it = store.find_item(d, p[3])
        if not it:
            return
        rows = [[btn("💲 Narxi", f"ed:f:{d['id']}:{it['id']}:price"), btn("🔤 Nomi", f"ed:f:{d['id']}:{it['id']}:name"),
                 btn("🏷 Kodi", f"ed:f:{d['id']}:{it['id']}:code")],
                [btn("📂 Guruhi", f"ed:f:{d['id']}:{it['id']}:category"),
                 btn("🗑 O'chirish", f"ed:rm:{d['id']}:{it['id']}")]]
        await bot.send(uid, f"<b>{esc(it['name'])}</b>\nKodi: {esc(it.get('code') or '—')}\nNarxi: {esc(it['price'])}\n"
                            f"Guruhi: {esc(c['name'])}", reply_markup=ikb(rows))
    elif act == "f":
        sessions[uid] = {"await": "edit_field", "design": p[2], "item": p[3], "field": p[4]}
        label = {"price": "yangi narxni (masalan 7.5$)", "name": "yangi nomni", "code": "yangi kodni",
                 "category": "guruh nomini"}[p[4]]
        await bot.send(uid, f"{label.capitalize()} yozing:")
    elif act == "rm":
        d = store.designs[p[2]]
        _, it = store.find_item(d, p[3])
        if it:
            await bot.send(uid, f"«{esc(it['name'])}» jadvaldan o'chirilsinmi?",
                           reply_markup=ikb([[btn("Ha, o'chirish", f"ed:rmy:{p[2]}:{p[3]}"), btn("Yo'q", "cancel")]]))
    elif act == "rmy":
        ops.delete_item(p[2], p[3])
        await bot.send(uid, "🗑 O'chirildi. Jadval yangilanadi.")
    elif act == "ro":
        await botextra.show_order(uid, store.designs[p[2]], sessions)
    elif act == "rn":
        sessions[uid] = {"await": "rename", "design": p[2]}
        await bot.send(uid, "Yangi nomni yozing:")
    elif act == "rp":
        await publisher.publish(p[2], repost=True)
        await bot.send(uid, "📤 Qayta joylandi.")
    elif act == "del":
        d = store.designs[p[2]]
        await bot.send(uid, f"«{esc(d['title'])}» katalogdan va guruhdan o'chirilsinmi?",
                       reply_markup=ikb([[btn("Ha, o'chirish", f"ed:dely:{p[2]}"), btn("Yo'q", "cancel")]]))
    elif act == "dely":
        ops.delete_design(p[2])
        await bot.send(uid, "🗑 O'chirildi.")
    elif act == "ag":
        d = store.designs[p[2]]
        rows = []
        for name in store.settings["agents"]:
            on = name in d.get("agents", [])
            rows.append([btn(("✅ " if on else "▫️ ") + name, f"ed:agt:{d['id']}:{name}")])
        await bot.send(uid, "Qaysi agentlar tugmasi chiqsin?", reply_markup=ikb(rows))
    elif act == "agt":
        d = store.designs[p[2]]
        ag = list(d.get("agents", []))
        if p[3] in ag:
            ag.remove(p[3])
        else:
            ag.append(p[3])
        ops.set_agents(d["id"], ag)
        rows = [[btn(("✅ " if n in ag else "▫️ ") + n, f"ed:agt:{d['id']}:{n}")] for n in store.settings["agents"]]
        try:
            await bot.call("editMessageReplyMarkup", chat_id=uid, message_id=cb["message"]["message_id"],
                           reply_markup=ikb(rows))
        except TelegramError:
            pass


async def edit_field_text(uid, sess, text):
    f = sess["field"]
    warns = ops.edit_item(sess["design"], sess["item"], **{f: text.strip()})
    sessions.pop(uid, None)
    msg = "✅ O'zgartirildi. Jadval yangilanadi."
    if warns:
        msg += "\n\n⚠️ Nazoratchi: " + "; ".join(esc(w) for w in warns)
    await bot.send(uid, msg, reply_markup=main_menu())


# ---------------- yangi mahsulot qo'shish ----------------
async def add_start(uid):
    rows = [[btn(s["title"], f"ad:sec:{s['key']}") for s in design_sections()]]
    await bot.send(uid, "➕ Qaysi bo'limga yangi mahsulot?", reply_markup=ikb(rows))


async def add_callback(uid, data, cb):
    p = data.split(":")
    await bot.answer_cb(cb["id"])
    if p[1] == "sec":
        ds = store.designs_in(p[2])
        rows = grid([btn(short(d), f"ad:d:{d['id']}") for d in ds], 3)
        await bot.send(uid, "Qaysi dizaynga?", reply_markup=ikb(rows))
    elif p[1] == "d":
        d = store.designs[p[2]]
        await bot.send(uid, f"➕ <b>{esc(d['title'])}</b> ga qanday qo'shamiz?",
                       reply_markup=ikb([[btn("📚 Mavjud mahsulotlardan (tanlab)", f"ad:old:{d['id']}")],
                                         [btn("🆕 Yangi mahsulot (yozib)", f"ad:new:{d['id']}")],
                                         [btn("❌ Bekor", "cancel")]]))
    elif p[1] == "old":
        await botextra.start_pick(uid, store.designs[p[2]], sessions)
    elif p[1] == "new":
        d = store.designs[p[2]]
        cats = ", ".join(c["name"] for c in d["categories"])
        sessions[uid] = {"await": "add_items", "design": d["id"]}
        await bot.send(uid, f"<b>{esc(d['title'])}</b> ga yangi mahsulot(lar)ni yozing — har biri alohida qatorda:\n\n"
                            "<code>Guruh | Nomi | Kodi | Narxi</code>\n"
                            "Masalan: <code>Aval | Aval kv 15 | YA5999 | 17$</code>\n\n"
                            f"<i>Mavjud guruhlar: {esc(cats)}</i>")


async def add_items_text(uid, sess, text):
    d = store.designs[sess["design"]]
    added, warns, bad = [], [], []
    for line in text.splitlines():
        parts = [x.strip() for x in line.split("|")]
        if len(parts) == 4:
            cat, name, code, price = parts
        elif len(parts) == 3:
            cat, name, price = parts
            code = ""
        else:
            if line.strip():
                bad.append(line.strip())
            continue
        try:
            it, w = ops.add_item(d["id"], cat, name, code, price)
        except ops.OpError as e:
            warns.append(str(e))
            continue
        added.append(it["name"])
        warns += [f"{it['name']}: {x}" for x in w]
    sessions.pop(uid, None)
    msg = f"✅ Qo'shildi: {len(added)} ta ({esc(', '.join(added))}). 🆕 belgisi {config.NEW_DAYS} kun turadi."
    if bad:
        msg += "\n\n❓ Tushunilmagan qatorlar:\n" + "\n".join(esc(b) for b in bad)
    if warns:
        msg += "\n\n⚠️ Nazoratchi: \n" + "\n".join(f"– {esc(w)}" for w in warns)
    await bot.send(uid, msg, reply_markup=main_menu())


# =====================================================================
#                          SOZLAMALAR
# =====================================================================
async def settings_menu(uid):
    st = store.settings
    if not store.is_owner(uid):
        await bot.send(uid, "Sozlamalarni faqat bot egasi o'zgartira oladi.")
        return
    admins = []
    rev = {v: k for k, v in store.s["users"].items()}
    for a in st.get("admins", []):
        admins.append("@" + rev.get(a, str(a)))
    agents = "\n".join(f"• {esc(n)} → @{esc(u) if u else '—'}" for n, u in st["agents"].items())
    topics = "\n".join(f"• {v}: {'✅' if st['topics'].get(k) else '❌'}" for k, v in config.TOPIC_KEYS.items())
    text = (f"⚙️ <b>Sozlamalar</b>\n\n<b>Adminlar:</b> {esc(', '.join(admins)) or 'yo`q'}\n\n"
            f"<b>Agentlar:</b>\n{agents}\n\n<b>Guruh bo'limlari:</b>\n{topics}")
    rows = [[btn("➕ Admin qo'shish", "se:aa"), btn("➖ Adminni olib tashlash", "se:ar")],
            [btn("👥 Agent username", "se:ag")],
            *([[btn("🔁 Avto-aylantirish holati", "se:rot")]] if config.ROTATION else
              [[btn("📤 Katalogni guruhga joylash", "se:pub")]]),
            *([] if config.ROTATION else [[btn("🔁 Hamma postlarni qayta joylash", "se:rep")]]),
            [btn("🔒 Katalog: faqat guruh a'zolari (ochish)" if st.get("private", True)
                 else "🔓 Katalog: hamma uchun (yopish)", "se:priv")],
            [btn("📌 Katalog havolasini tepaga qo'yish", "se:pin")],
            [btn("🖼 Mahsulot rasmlarini qayta tayyorlash", "se:relabel")],
            [btn("💾 Zaxira faylini olish", "se:bk")]]
    await bot.send(uid, text, reply_markup=ikb(rows))


async def settings_callback(uid, data, cb):
    await bot.answer_cb(cb["id"])
    if not store.is_owner(uid):
        return
    p = data.split(":")
    if p[1] == "aa":
        sessions[uid] = {"await": "admin_add"}
        await bot.send(uid, "Yangi adminning @username'ini yozing.\n"
                            "<i>Muhim: u odam avval botga /start yozgan bo'lishi kerak.</i>")
    elif p[1] == "ar":
        rev = {v: k for k, v in store.s["users"].items()}
        rows = [[btn("@" + rev.get(a, str(a)), f"se:rm:{a}")] for a in store.settings.get("admins", [])]
        await bot.send(uid, "Kimni olib tashlaymiz?", reply_markup=ikb(rows) if rows else None)
    elif p[1] == "rm":
        a = int(p[2])
        if a in store.settings["admins"]:
            store.settings["admins"].remove(a)
            store.save()
            try:
                await bot.call("setChatMenuButton", chat_id=a, menu_button={"type": "default"})
                await bot.call("deleteMyCommands", scope={"type": "chat", "chat_id": a})
            except TelegramError:
                pass
        await bot.send(uid, "✅ Olib tashlandi.")
    elif p[1] == "ag":
        rows = [[btn(n, f"se:agn:{n}")] for n in store.settings["agents"]]
        rows.append([btn("➕ Yangi agent", "se:agnew")])
        await bot.send(uid, "Qaysi agent?", reply_markup=ikb(rows))
    elif p[1] == "agn":
        sessions[uid] = {"await": "agent_user", "agent": p[2]}
        await bot.send(uid, f"{esc(p[2])} ning Telegram username'ini yozing (masalan @ibroxim_4346):")
    elif p[1] == "agnew":
        sessions[uid] = {"await": "agent_new"}
        await bot.send(uid, "Yangi agent: <code>Ism @username</code> ko'rinishida yozing:")
    elif p[1] == "pub":
        no_photo = [d for d in store.designs.values() if not d.get("deleted") and not d.get("photos")
                    and d["section"] in ("lenon", "boden")]
        txt = "Guruhga hali bot joylamagan barcha dizayn va naborlar joylanadi."
        if no_photo:
            txt += (f"\n\n⚠️ {len(no_photo)} ta dizaynning rasmi botda yo'q. Avval guruhdagi eski postlarni botga "
                    "<b>forward</b> qiling yoki rasmlarni izohiga dizayn nomini yozib yuboring — bot rasmlarni o'zi biriktiradi.")
        await bot.send(uid, txt, reply_markup=ikb([[btn("📤 Ha, joylash", "se:puby")], [btn("❌ Bekor", "cancel")]]))
    elif p[1] == "puby":
        asyncio.get_event_loop().create_task(publish_all(uid))
    elif p[1] == "rot":
        import rotation
        await bot.send(uid, rotation.status_text())
    elif p[1] == "priv":
        store.settings["private"] = not store.settings.get("private", True)
        store.save()
        asyncio.get_event_loop().create_task(apply_privacy(uid))
    elif p[1] == "relabel":
        import relabel
        asyncio.get_event_loop().create_task(relabel.run(uid))
    elif p[1] == "pin":
        asyncio.get_event_loop().create_task(pin_catalog(uid))
    elif p[1] == "rep":
        asyncio.get_event_loop().create_task(publish_all(uid, repost=True))
    elif p[1] == "bk":
        await on_menu(uid, "/zaxira")


async def pdf_command(uid, q):
    """/pdf 01-AL — dizayn katalogini PDF qilib yuboradi."""
    d = store.find_design(q) if q else None
    if not d:
        await bot.send(uid, "Qaysi dizayn? Masalan: <code>/pdf 01-AL</code> yoki <code>/pdf BODEN 003</code>")
        return
    import webapi
    note = await bot.send(uid, f"⏳ «{esc(d['title'])}» PDF tayyorlanmoqda…")
    try:
        pdf, name = await webapi.cached_pdf(d["id"])
        await bot.call("sendDocument", files={"document": (name, pdf, "application/pdf")}, chat_id=str(uid),
                       caption=f"📄 {d['title']} — katalog (PDF)")
    except Exception as e:  # noqa
        await checker.report_error("PDF tayyorlanmadi", e)
        await bot.send(uid, "⚠️ PDF tayyorlab bo'lmadi.")
    await bot.safe_delete(uid, note["message_id"])


async def sync_menu_buttons():
    """Pastki «📒 Katalog» tugmasi va buyruqlar: maxfiy rejimda faqat adminlarga ko'rinadi."""
    st = store.settings
    url = webapp_url()
    admin_cmds = [{"command": "start", "description": "Asosiy menyu"},
                  {"command": "hisobot", "description": "Hisobot"},
                  {"command": "tekshir", "description": "Nazoratchi tekshiruvi"},
                  {"command": "qolmadi", "description": "Qolmadi / Keldi belgilash"},
                  {"command": "pdf", "description": "Dizayn katalogini PDF qilish (/pdf 01-AL)"},
                  {"command": "rasm", "description": "Dizayn rasmlarini yuborish (/rasm 01-AL)"},
                  {"command": "arxiv", "description": "Rasmlarni kod bilan ZIP arxivda olish (/arxiv 01-AL)"}]
    if config.ROTATION:
        admin_cmds += [{"command": "navbat", "description": "Avto-aylantirish holati"},
                       {"command": "aylantir", "description": "Navbatdagi tovarni hozir guruhga joylash"},
                       {"command": "guruhga", "description": "Tanlangan tovarni hozir joylash (/guruhga SR95)"}]
    private = st.get("private", True)
    try:
        if private:
            await bot.call("setChatMenuButton", menu_button={"type": "default"})
            await bot.call("setMyCommands", commands=[{"command": "start", "description": "Boshlash"}])
        elif url:
            await bot.call("setChatMenuButton", menu_button={"type": "web_app", "text": "📒 Katalog",
                                                             "web_app": {"url": url}})
            await bot.call("setMyCommands", commands=admin_cmds[:1])
    except TelegramError:
        pass
    for a in set(st.get("admins", []) + ([st["owner_id"]] if st.get("owner_id") else [])):
        try:
            if url:
                await bot.call("setChatMenuButton", chat_id=a, menu_button={
                    "type": "web_app", "text": "📒 Katalog", "web_app": {"url": url}})
            await bot.call("setMyCommands", commands=admin_cmds, scope={"type": "chat", "chat_id": a})
        except TelegramError:
            pass


async def apply_privacy(uid=None):
    """Maxfiylik o'zgarganda: tugmalar, guruhdagi havolalar va mahkamlangan xabarlarni moslaydi."""
    st = store.settings
    private = st.get("private", True)
    await sync_menu_buttons()
    st["privacy_applied"] = private
    store.save()
    if uid:
        await bot.send(uid, "🔒 Katalog endi faqat Lenon 55 guruhi a'zolari va adminlar uchun. Begonalar ocha olmaydi."
                       if private else "🔓 Katalog hamma uchun ochildi.")


async def pin_catalog(uid):
    """Har bir bo'limga katalogdan foydalanish yo'riqnomasini (katalog tugmasi bilan) qo'yib, tepaga mahkamlaydi."""
    st = store.settings
    if not st.get("group_id") or not st["topics"]:
        await bot.send(uid, "Avval guruh bo'limlarini ulang (/bolim).")
        return
    ok, errs = 0, []
    for key, thread in st["topics"].items():
        if not thread:
            continue
        try:
            await publisher.publish_guide(key)
            ok += 1
        except TelegramError as e:
            errs.append(f"{config.TOPIC_KEYS.get(key, key)}: {e.description}")
        await asyncio.sleep(1.5)
    msg = f"📌 {ok} ta bo'limga yo'riqnoma va katalog havolasi qo'yildi."
    if errs:
        msg += "\n⚠️ " + "\n⚠️ ".join(esc(x) for x in errs)
        if any("not enough rights" in x.lower() for x in errs):
            msg += "\n\nBotga guruhda «Xabarlarni mahkamlash» (Pin messages) huquqini bering."
    await bot.send(uid, msg)


_publish_running = {"on": False}


async def publish_all(uid, repost=False):
    """Guruhga joylash. repost=True — TO'LIQ QAYTA QURISH: avval botning barcha postlari o'chiriladi,
    keyin har bo'lim tartib bilan joylanadi: jadval → uning PDF i → … → jadvallar ro'yxati → yo'riqnoma."""
    if config.ROTATION:
        await bot.send(uid, "Bu guruhda tovarlar har soatda avtomatik aylantirib joylanadi (08:00–22:00).\n"
                            "/navbat — holat, /aylantir — navbatdagisini hozir joylash.")
        return
    if _publish_running["on"]:
        await bot.send(uid, "⏳ Joylash allaqachon ketyapti — tugashini kuting.")
        return
    _publish_running["on"] = True
    publisher.REBUILD["on"] = True   # fondagi yangilashlar shu payt to'xtab turadi
    try:
        sec_order = {"lenon": 0, "boden": 1, "nabor44": 2, "nabor33": 3, "nabor39": 4, "rasmlar": 5}
        if repost:
            note = await bot.send(uid, "🧹 1-qadam: guruhdagi botning barcha eski postlari o'chirilmoqda "
                                       "(jadvallar, PDF lar, ro'yxatlar, dublikatlar)… Bir necha daqiqa.")
            n = await publisher.wipe_group(uid)
            await bot.send(uid, f"✅ {n} ta eski post o'chirildi.")
        todo = [d for d in sorted(store.designs.values(),
                                  key=lambda x: (sec_order.get(x["section"], 9), x.get("order", 0)))
                if not d.get("deleted") and not d.get("msg")
                and not (store.section(d["section"])["kind"] in ("nabor", "photos") and not d.get("photos"))]
        await bot.send(uid, f"📤 2-qadam: {len(todo)} ta jadval tartib bilan joylanmoqda (har birining tagida PDF)… "
                            "Taxminan 10–15 daqiqa.")
        ok = 0
        for i, d in enumerate(todo, 1):
            try:
                await publisher.publish(d["id"])
                ok += 1
            except publisher.NotConfigured as e:
                await bot.send(uid, f"⚠️ {esc(e)}")
                break
            except Exception as e:  # noqa
                await checker.report_error(f"{d['title']} joylanmadi", e)
            if i % 5 == 0:
                await bot.send(uid, f"⏳ {i}/{len(todo)}", disable_notification=True)
            await asyncio.sleep(3)
        # har bo'lim oxiri: jadvallar ro'yxati, keyin yo'riqnoma + katalog havolasi
        for key in ("lenon", "boden", "nabor44", "nabor33", "nabor39"):
            if not store.settings["topics"].get(key):
                continue
            try:
                await publisher.finish_section(key)
            except Exception as e:  # noqa
                await checker.report_error(f"{key}: ro'yxat/yo'riqnoma joylanmadi", e)
            await asyncio.sleep(2)
        await bot.send(uid, f"✅ Tayyor: {ok}/{len(todo)} ta jadval joylandi.\n"
                            "Har bo'lim oxirida — jadvallar ro'yxati va katalogdan foydalanish yo'riqnomasi.")
    finally:
        publisher.REBUILD["on"] = False
        _publish_running["on"] = False


async def admin_add_text(uid, text):
    sessions.pop(uid, None)
    uname = text.strip().lstrip("@").lower()
    tid = store.s["users"].get(uname)
    if not tid:
        await bot.send(uid, f"@{esc(uname)} hali botga /start yozmagan. Unga botni ochib /start bosishini ayting, "
                            "keyin qaytadan qo'shing.", reply_markup=main_menu())
        return
    if tid not in store.settings["admins"]:
        store.settings["admins"].append(tid)
        store.save()
        asyncio.get_event_loop().create_task(sync_menu_buttons())
    await bot.send(uid, f"✅ @{esc(uname)} admin qilindi.", reply_markup=main_menu())
    try:
        await bot.send(tid, "Sizga LENON katalog botida admin huquqi berildi ✅", reply_markup=main_menu())
    except TelegramError:
        pass


# =====================================================================
#                        TUGMALAR (callback)
# =====================================================================
async def on_callback(cb):
    uid = cb["from"]["id"]
    data = cb.get("data", "")
    if not store.is_admin(uid):
        await bot.answer_cb(cb["id"], "Bu tugma faqat adminlar uchun.", True)
        return
    if data == "noop":
        await bot.answer_cb(cb["id"])
        return
    if data == "cancel":
        sessions.pop(uid, None)
        await bot.answer_cb(cb["id"], "Bekor qilindi")
        await bot.send(uid, "Bekor qilindi. Asosiy menyu 👇", reply_markup=main_menu())
        return
    pre = data.split(":")[0]
    if pre == "ph":
        await photo_callback(uid, data, cb)
    elif pre == "nd":
        await new_design_callback(uid, data, cb)
    elif pre == "sk":
        await stock_callback(uid, data, cb)
    elif pre == "qs":
        await quick_callback(uid, data, cb)
    elif pre == "ed":
        await edit_callback(uid, data, cb)
    elif pre == "ad":
        await add_callback(uid, data, cb)
    elif pre == "se":
        await settings_callback(uid, data, cb)
    elif pre == "sr":
        await botextra.search_callback(uid, data, cb, sessions)
    elif pre == "pk":
        await botextra.pick_callback(uid, data, cb, sessions, main_menu)
    elif pre == "rep":
        await bot.answer_cb(cb["id"])
        await bot.send(uid, await report.build(int(data.split(":")[1])))
    else:
        await bot.answer_cb(cb["id"])
_orig_on_message = on_message
async def on_message(m):
    frm = m.get("from") or {}
    # anonim admin (guruh nomidan yozilgan) xabarlari bot emas — ularni o'tkazib yubormaymiz
    if (frm.get("is_bot") and frm.get("id") != ANON_BOT_ID) or m.get("pinned_message"):
        return None
    return await _orig_on_message(m)
