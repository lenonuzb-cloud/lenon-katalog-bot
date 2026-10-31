"""LENON_UZ: haftalik post jadvali.

Egasi mini ilovada har bir hafta kuniga qaysi tovarlar chiqishini tanlaydi (masalan dushanba — butun
«Nabor 46»). O'sha kuni tanlangan tovarlar 08:00–22:00 oralig'ida teng bo'lib chiqadi: 15 ta bo'lsa —
har soatda, 20 ta bo'lsa — har 44 daqiqada. Jadvali bo'sh kunlarda oddiy navbat (soatiga 1 ta) ishlaydi."""
import logging
from datetime import datetime

import config
from store import store

log = logging.getLogger("schedule")
DAYS = ["Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba"]
SHORT = ["Du", "Se", "Ch", "Pa", "Ju", "Sh", "Ya"]
LATE = 40  # daqiqa: server uxlab qolib kechikkan post shuncha vaqtgacha chiqariladi, undan keyin o'tkaziladi


def span() -> int:
    return (config.ROT_LAST_HOUR - config.ROT_FIRST_HOUR) * 60


def week() -> dict:
    w = store.settings.setdefault("week", {})
    for i in range(7):
        w.setdefault(str(i), [])
    return w


def _ok(did) -> bool:
    d = store.designs.get(did)
    if not d or d.get("deleted") or d.get("pinned"):
        return False
    import naborlar
    return not (naborlar.is_whole(d) and not d.get("in_stock", True))


def day_list(wd: int) -> list:
    return [x for x in week().get(str(wd), []) if _ok(x)]


def times(n: int) -> list:
    """Har bir post vaqti (08:00 dan boshlab daqiqalarda)."""
    if n <= 0:
        return []
    if n == 1:
        return [0]
    return [round(i * span() / (n - 1)) for i in range(n)]


def hhmm(m: int) -> str:
    m += config.ROT_FIRST_HOUR * 60
    return f"{m // 60:02d}:{m % 60:02d}"


def interval_text(n: int) -> str:
    if n <= 1:
        return "1 ta post — 08:00 da"
    step = span() / (n - 1)
    return f"{n} ta post — har {int(step // 60)} soat {int(step % 60)} daqiqada" if step >= 60 \
        else f"{n} ta post — har {round(step)} daqiqada"


def active_today() -> bool:
    return bool(day_list(datetime.now(config.TZ).weekday()))


async def tick() -> bool:
    """Bugun jadval bo'lsa — vaqti kelgan postni chiqaradi va True qaytaradi (oddiy navbat ishlamaydi)."""
    now = datetime.now(config.TZ)
    lst = day_list(now.weekday())
    if not lst:
        return False
    today = now.strftime("%Y-%m-%d")
    st = store.settings.setdefault("week_state", {})
    if st.get("date") != today:
        st.clear()
        st.update({"date": today, "done": []})
    minute = (now.hour - config.ROT_FIRST_HOUR) * 60 + now.minute
    if minute < 0 or minute > span() + LATE:
        return True
    tms = times(len(lst))
    done = set(st["done"])
    for i, (did, t) in enumerate(zip(lst, tms)):
        key = did
        if key in done or t > minute:
            continue
        st["done"].append(key)
        store.save()
        if minute - t > LATE:
            continue  # juda kechikdi — o'tkazib yuboramiz
        import rotation
        await rotation.post_design(did, "haftalik jadval")
        return True  # bir daqiqada bittadan
    return True


def set_day(wd: int, ids: list):
    w = week()
    alive = {d["id"] for d in store.designs.values() if not d.get("deleted")}
    w[str(wd)] = [x for x in dict.fromkeys(ids) if x in alive]
    store.save()


def summary() -> dict:
    out = {}
    for i in range(7):
        n = len(day_list(i))
        out[str(i)] = {"n": n, "text": interval_text(n) if n else "Jadval yo'q — oddiy navbat (soatiga 1 ta)"}
    return out


def today_text() -> str:
    now = datetime.now(config.TZ)
    lst = day_list(now.weekday())
    if not lst:
        return ""
    tms = times(len(lst))
    done = set((store.settings.get("week_state") or {}).get("done", [])) \
        if (store.settings.get("week_state") or {}).get("date") == now.strftime("%Y-%m-%d") else set()
    rows = []
    for i, (did, t) in enumerate(zip(lst, tms)):
        mark = "✅" if did in done else "🕒"
        import publisher
        rows.append(f"{mark} {hhmm(t)} — {publisher.esc(store.designs[did]['title'])}")
    return (f"📅 <b>Bugun ({DAYS[now.weekday()]}) haftalik jadval:</b> {interval_text(len(lst))}\n"
            + "\n".join(rows[:40]))
