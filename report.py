"""8-agent: bajarilgan ishlar haqida hisobot tayyorlaydi."""
import html
from collections import defaultdict
from datetime import datetime

import config
import checker
from notify import _group
from store import store, now

KIND_TITLES = [
    ("new", "🆕 Yangi qo'shildi"),
    ("qolmadi", "❌ Qolmadi deb belgilandi"),
    ("keldi", "✅ Keldi deb belgilandi"),
    ("edit", "✏️ Tahrirlandi"),
    ("delete", "🗑 O'chirildi"),
    ("publish", "📤 Guruhga joylandi"),
]


def _esc(t):
    return html.escape(str(t), quote=False)


async def build(hours: int = 24) -> str:
    t0 = now() - hours * 3600
    evs = [e for e in store.s["events"] if e["ts"] >= t0]
    by = defaultdict(list)
    for e in evs:
        by[e["kind"]].append(e["text"])
    stamp = datetime.now(config.TZ).strftime("%d.%m.%Y %H:%M")
    period = "bugungi" if hours <= 24 else f"so'nggi {hours // 24} kunlik"
    parts = [f"📊 <b>Hisobot</b> — {period} ({stamp})"]

    summary = []
    for k, title in KIND_TITLES:
        if by.get(k):
            summary.append(f"{title}: <b>{len(by[k])}</b>")
    parts.append("\n".join(summary) if summary else "Bu davrda o'zgarish bo'lmadi.")

    for k, title in KIND_TITLES[:5]:
        lst = _group(by.get(k) or [])
        if lst:
            shown = "\n".join(f"• {_esc(x)}" for x in lst[:15])
            more = f"\n… va yana {len(lst) - 15} ta" if len(lst) > 15 else ""
            parts.append(f"<b>{title}:</b>\n{shown}{more}")

    # hozir tugagan mahsulotlar
    out = []
    for d in store.designs.values():
        if d.get("deleted"):
            continue
        if not d.get("in_stock", True):
            out.append(f"{d['title']} ({store.section(d['section'])['title']})")
        for c in d["categories"]:
            for it in c["items"]:
                if not it.get("in_stock", True):
                    out.append(f"{it['name']} ({d['title']})")
    out = _group(out)
    if out:
        shown = "\n".join(f"• {_esc(x)}" for x in out[:30])
        more = f"\n… va yana {len(out) - 30} ta" if len(out) > 30 else ""
        parts.append(f"📦 <b>Hozir qolmagan:</b>\n{shown}{more}")
    else:
        parts.append("📦 Hozir qolmagan mahsulot yo'q.")

    # umumiy holat
    total_designs = sum(1 for d in store.designs.values() if not d.get("deleted"))
    total_items = sum(len(c["items"]) for d in store.designs.values() if not d.get("deleted") for c in d["categories"])
    parts.append(f"📒 Katalogda: <b>{total_designs}</b> ta dizayn/nabor, <b>{total_items}</b> ta jadval qatori")

    # nazoratchi
    errs = by.get("error", [])
    probs = checker.audit()
    sc = await checker.self_check()
    ctl = []
    if errs:
        ctl.append(f"Xatolar: {len(errs)} ta (oxirgisi: {_esc(errs[-1][:150])})")
    if probs:
        ctl.append(f"Jadvallarda shubhali joylar: {len(probs)} ta — batafsil: /tekshir")
    ctl += [_esc(x) for x in sc]
    parts.append("🛠 <b>Nazoratchi:</b>\n" + ("\n".join(ctl) if ctl else "Hammasi joyida ✅"))
    return "\n\n".join(parts)[:4000]
