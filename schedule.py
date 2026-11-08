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


def day_times(wd: int) -> dict:
    """Egasi qo'lda qo'ygan vaqtlar: {design_id: daqiqa (08:00 dan)}."""
    raw = (store.settings.get("week_times") or {}).get(str(wd)) or {}
    out = {}
    for did, hm in raw.items():
        try:
            h, m = map(int, str(hm).split(":"))
            out[did] = (h - config.ROT_FIRST_HOUR) * 60 + m
        except Exception:  # noqa
            pass
    return out


def plan(wd: int) -> list:
    """Kun rejasi: [(daqiqa, design_id, qo'lda_qo'yilganmi)] vaqt bo'yicha. Vaqti yozilmaganlar
    08:00–22:00 ga teng bo'linadi."""
    lst = day_list(wd)
    fixed = {k: v for k, v in day_times(wd).items() if k in lst}
    auto = [x for x in lst if x not in fixed]
    res = [(t, did, True) for did, t in fixed.items()] + [(t, did, False) for t, did in zip(times(len(auto)), auto)]
    res.sort(key=lambda r: r[0])
    return res


def set_time(wd: int, did: str, hm: str):
    """Tovarga aniq vaqt qo'yadi («" — avtomatik»)."""
    import re as _re
    wt = store.settings.setdefault("week_times", {}).setdefault(str(wd), {})
    hm = (hm or "").strip()
    if not hm:
        wt.pop(did, None)
    else:
        m = _re.fullmatch(r"(\d{1,2})[:.](\d{2})", hm)
        if not m:
            raise ValueError("Vaqtni 09:30 ko'rinishida yozing")
        h, mi = int(m.group(1)), int(m.group(2))
        if not (config.ROT_FIRST_HOUR <= h <= config.ROT_LAST_HOUR) or mi > 59 or (h == config.ROT_LAST_HOUR and mi > 0):
            raise ValueError(f"Vaqt {config.ROT_FIRST_HOUR:02d}:00 dan {config.ROT_LAST_HOUR:02d}:00 gacha bo'lsin")
        wt[did] = f"{h:02d}:{mi:02d}"
    store.save()


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
    done = set(st["done"])
    for t, did, _ in plan(now.weekday()):
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
    done = set((store.settings.get("week_state") or {}).get("done", [])) \
        if (store.settings.get("week_state") or {}).get("date") == now.strftime("%Y-%m-%d") else set()
    rows = []
    for t, did, fixed in plan(now.weekday()):
        mark = "✅" if did in done else ("📌" if fixed else "🕒")
        import publisher
        rows.append(f"{mark} {hhmm(t)} — {publisher.esc(store.designs[did]['title'])}")
    return (f"📅 <b>Bugun ({DAYS[now.weekday()]}) haftalik jadval:</b> {interval_text(len(lst))}\n"
            + "\n".join(rows[:40]))


def section_days(key: str) -> list:
    """Bo'lim qaysi kunlarda turibdi: har kun uchun «all» / «some» / «none»."""
    ids = [d["id"] for d in store.designs_in(key) if not d.get("pinned")]
    out = []
    for i in range(7):
        day = set(week()[str(i)])
        n = sum(1 for x in ids if x in day)
        out.append("all" if ids and n == len(ids) else ("some" if n else "none"))
    return out


def set_section_days(key: str, days: list):
    """Bo'limning hamma tovarini tanlangan kunlarga qo'yadi, qolgan kunlardan olib tashlaydi."""
    ids = [d["id"] for d in store.designs_in(key) if not d.get("pinned")]
    idset = set(ids)
    w = week()
    for i in range(7):
        cur = [x for x in w[str(i)] if x not in idset]
        if i in days:
            cur += ids
        w[str(i)] = cur
    store.save()
