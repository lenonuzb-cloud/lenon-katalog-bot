"""4-agent: nazoratchi. Ma'lumotlardagi xatolarni topadi, boshqa agentlarning ishidagi
xatolarni ushlaydi va egasiga xabar beradi."""
import asyncio
import logging
import re
import time
import traceback
from collections import defaultdict

import config
from store import store, now
from tg import bot, TelegramError

log = logging.getLogger("checker")

PRICE_RE = re.compile(r"^\d+(\.\d+)?\$$")
CODE_RE = re.compile(r"^[A-Z]{2,3}\d{4}$")

_last_alert = 0.0
_alert_buf: list[str] = []
_alert_task = None


def normalize_price(p: str) -> str:
    """'9#', '2.2 $', '5,5$', '12' kabi yozuvlarni '9$', '2.2$', '5.5$', '12$' ga keltiradi."""
    s = (p or "").strip().replace(" ", "").replace(",", ".").replace("#", "$")
    s = s.replace("usd", "$").replace("USD", "$")
    if re.fullmatch(r"\d+(\.\d+)?", s):
        s += "$"
    return s


def normalize_code(c: str) -> str:
    return (c or "").strip().upper().replace(" ", "")


def check_item(name, code, price, has_codes=True) -> list[str]:
    w = []
    if not (name or "").strip():
        w.append("nomi bo'sh")
    if not PRICE_RE.match(price or ""):
        w.append(f"narx noto'g'ri: «{price}»")
    if has_codes and code and not CODE_RE.match(code):
        w.append(f"kod g'alati: «{code}» (odatda 2–3 harf + 4 raqam)")
    return w


def audit() -> list[str]:
    """Butun katalogni tekshiradi. Topilgan muammolar ro'yxatini qaytaradi."""
    probs = []
    by_code = defaultdict(set)      # kod -> nomlar
    price_by = defaultdict(set)     # (kod, nom) -> narxlar
    no_photo = []
    for d in store.designs.values():
        if d.get("deleted"):
            continue
        sec = store.section(d["section"])
        if not sec:
            continue
        if sec["kind"] == "design":
            has_codes = any(it.get("code") for c in d["categories"] for it in c["items"])
            seen = {}
            for c in d["categories"]:
                for it in c["items"]:
                    for w in check_item(it["name"], it.get("code"), it["price"], has_codes):
                        probs.append(f"{d['title']} → {it['name']}: {w}")
                    if it.get("code"):
                        if it["code"] in seen and seen[it["code"]].lower() != it["name"].lower():
                            probs.append(f"{d['title']}: {it['code']} kodi ikki xil mahsulotda — «{seen[it['code']]}» va «{it['name']}»")
                        seen[it["code"]] = it["name"]
                        by_code[it["code"]].add(it["name"].lower().replace(" ", ""))
                        price_by[(it["code"], it["name"].lower().replace(" ", ""))].add(it["price"])
            if not d["categories"]:
                probs.append(f"{d['title']}: jadval bo'sh")
        if not d.get("photos"):
            no_photo.append(d["title"])
    for code, names in by_code.items():
        if len(names) > 1:
            probs.append(f"{code} kodi turli nomlar bilan yozilgan: {', '.join(sorted(names))}")
    for (code, name), prices in price_by.items():
        if len(prices) > 1:
            probs.append(f"{code} ({name}) turli dizaynlarda turli narxda: {', '.join(sorted(prices))}")
    if no_photo:
        probs.append(f"Rasmi yo'q ({len(no_photo)} ta): " + ", ".join(no_photo[:12]) + (" …" if len(no_photo) > 12 else ""))
    return probs


async def self_check() -> list[str]:
    """Botning o'zi to'g'ri ishlayotganini tekshiradi."""
    out = []
    st = store.settings
    if not st.get("owner_id"):
        out.append("❗ Bot egasi hali aniqlanmagan (egasi botga /start yozishi kerak).")
    gid = st.get("group_id")
    if not gid:
        out.append("❗ Guruh hali ulanmagan. Botni guruhga admin qilib qo'shing va bo'limlarda /bolim buyrug'ini yozing.")
    else:
        try:
            me = await bot.call("getChatMember", chat_id=gid, user_id=bot.me["id"])
            if me.get("status") != "administrator":
                out.append("❗ Bot guruhda admin emas — post joylay olmaydi.")
            else:
                if not me.get("can_delete_messages"):
                    out.append("⚠️ Botda xabar o'chirish huquqi yo'q.")
                if not me.get("can_manage_topics"):
                    out.append("⚠️ Botda bo'limlarni boshqarish huquqi yo'q (yopiq bo'limlarga yozish uchun kerak bo'lishi mumkin).")
        except TelegramError as e:
            out.append(f"❗ Guruhni tekshirib bo'lmadi: {e.description}")
        missing = [config.TOPIC_KEYS[k] for k in config.TOPIC_KEYS if not st["topics"].get(k)]
        if missing:
            out.append("⚠️ Sozlanmagan bo'limlar: " + ", ".join(missing))
    no_post = [d["title"] for d in store.designs.values()
               if not d.get("deleted") and not d.get("msg") and d["section"] != "rasmlar"]
    if no_post:
        out.append(f"ℹ️ Guruhga hali bot joylamagan dizaynlar: {len(no_post)} ta")
    if store.last_backup_error:
        out.append(f"❗ Zaxira nusxa saqlanmadi: {store.last_backup_error}")
    if st.get("rich_ok") is False:
        out.append("ℹ️ Boy (rich) xabarlar ishlamadi — jadvallar rasm ko'rinishida joylanmoqda.")
    return out


async def report_error(context: str, e: Exception):
    """Har qanday agentdagi xatoni yozib qo'yadi va egasiga (to'plab) xabar beradi."""
    global _alert_task
    desc = e.description if isinstance(e, TelegramError) else f"{type(e).__name__}: {e}"
    store.event("error", f"{context}: {desc}")
    log.error("%s\n%s", context, "".join(traceback.format_exception(e))[-1500:])
    _alert_buf.append(f"• {context}\n  <i>{desc[:300]}</i>")
    if _alert_task is None or _alert_task.done():
        _alert_task = asyncio.get_event_loop().create_task(_flush_alerts())


async def _flush_alerts():
    global _last_alert
    wait = max(5, 60 - (time.time() - _last_alert))
    await asyncio.sleep(wait)
    owner = store.settings.get("owner_id")
    if not owner or not _alert_buf:
        _alert_buf.clear()
        return
    text = "🛠 <b>Nazoratchi:</b> quyidagi muammolar bo'ldi\n\n" + "\n".join(_alert_buf[-10:])
    _alert_buf.clear()
    _last_alert = time.time()
    try:
        await bot.send(owner, text[:4000])
    except Exception:  # noqa
        pass


def validate_new_item(design, name, code, price) -> tuple[str, str, str, list[str]]:
    """Yangi/tahrirlangan mahsulotni tozalaydi va ogohlantirishlarni qaytaradi."""
    price = normalize_price(price)
    code = normalize_code(code)
    name = re.sub(r"\s+", " ", (name or "").strip())
    has_codes = any(it.get("code") for c in design["categories"] for it in c["items"]) or bool(code)
    warns = check_item(name, code, price, has_codes)
    if code:
        for c in design["categories"]:
            for it in c["items"]:
                if it.get("code") == code and it["name"].lower() != name.lower():
                    warns.append(f"{code} kodi bu dizaynda allaqachon «{it['name']}» uchun bor")
    return name, code, price, warns
