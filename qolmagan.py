"""/qolmaganlar — hozir qolmagan mahsulotlarni ko'rib chiqish va xohlasa guruhga e'lon qilish."""
import html

from store import store
from tg import bot


def build() -> str:
    by = []
    for d in store.designs.values():
        if d.get("deleted"):
            continue
        if d.get("in_stock") is False:
            by.append((d["title"], ["butun nabor"]))
            continue
        names = [it["name"] + (f" ({it['code']})" if it.get("code") else "")
                 for c in d["categories"] for it in c["items"] if not it.get("in_stock", True)]
        if names:
            by.append((d["title"], names))
    if not by:
        return ""
    lines = ["❌ <b>Hozir qolmagan mahsulotlar</b>", ""]
    for t, names in by:
        lines.append(f"<b>{html.escape(t)}</b>: " + ", ".join(html.escape(n) for n in names))
    return "\n".join(lines)


async def show(uid):
    text = build()
    if not text:
        await bot.send(uid, "✅ Hozir qolmagan mahsulot yo'q — hammasi bor.")
        return
    await bot.send(uid, text[:3900], reply_markup={"inline_keyboard": [
        [{"text": "📢 Guruhga (Muhokama) yuborish", "callback_data": "qm:post"}]]})


async def callback(uid, data, cb):
    await bot.answer_cb(cb["id"])
    st = store.settings
    gid, thread = st.get("group_id"), (st.get("topics") or {}).get("muhokama")
    text = build()
    if not gid or not thread:
        await bot.send(uid, "Avval guruhning Muhokama bo'limida <code>/bolim muhokama</code> deb yozing.")
        return
    if not text:
        await bot.send(uid, "Qolmagan mahsulot yo'q.")
        return
    await bot.call("sendMessage", chat_id=gid, message_thread_id=thread, text=text[:3900], parse_mode="HTML")
    await bot.send(uid, "✅ Guruhning Muhokama bo'limiga yuborildi.")
