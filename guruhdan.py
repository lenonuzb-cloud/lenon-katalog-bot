"""Guruhdagi postlardan dizayn/nabor rasmlarini katalogga qaytarish.

Bot guruhdagi har bir xabarni egasining chatiga jimgina forward qilib (darhol o'chiriladi),
undagi rasmlarni va sarlavhani o'qiydi. Sarlavhasi katalogdagi dizayn/naborga mos kelsa —
o'sha dizaynga rasmlar qaytariladi (agar katalogda rasmi kamroq bo'lsa)."""
import html
import logging
import re

from store import store
from tg import bot, TelegramError

log = logging.getLogger("guruhdan")


def _photos(obj, out):
    """Javob ichidagi barcha rasmlarni topadi (har bir rasmning eng katta o'lchami)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "photo" and isinstance(v, list) and v and isinstance(v[0], dict) and "file_id" in v[0]:
                big = max(v, key=lambda p: p.get("width", 0) * p.get("height", 0))
                if big["file_unique_id"] not in {u for _, u in out}:
                    out.append((big["file_id"], big["file_unique_id"]))
            else:
                _photos(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _photos(v, out)


def _texts(obj, out):
    if isinstance(obj, dict):
        for k in ("caption", "text"):
            if isinstance(obj.get(k), str) and obj[k].strip():
                out.append(obj[k])
        for k, v in obj.items():
            if isinstance(v, (dict, list)) and k not in ("reply_markup", "forward_origin", "from", "chat"):
                _texts(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _texts(v, out)


def _norm(s):
    return re.sub(r"[^0-9a-z]+", "", (s or "").lower())


def _match(text):
    """Matndan dizaynni aniqlaydi. Ro'yxat/yo'riqnoma/xabarnomalar uchun None."""
    t = " ".join(text.split())
    if not t or t.startswith(("📋", "📒", "📢", "📄")):
        return None
    designs = [d for d in store.designs.values() if not d.get("deleted")]
    m = re.search(r"nabor\s*:?\s*([A-Za-z]\d[\w-]*)", t, re.I)
    code = m.group(1) if m else None
    jami = re.search(r"jami\s*:?\s*(\d+)", t, re.I)
    if code or jami:
        if not code:
            code = t.split()[0]
        cands = [d for d in designs if _norm(d["title"]) == _norm(code)]
        if jami:
            sec = [d for d in cands if d["section"].endswith(jami.group(1))]
            cands = sec or cands
        return cands[0] if len(cands) == 1 else None
    head = _norm(t[:40])
    best = None
    for d in designs:
        n = _norm(d["title"])
        if n and head.startswith(n) and (best is None or len(n) > len(_norm(best["title"]))):
            best = d
    return best


ROW = re.compile(r"(?:^|\s)(\d{1,2})\s+(.+?)\s+(?:([A-Z]{2}\d{3,6})\s+)?(\d+(?:[.,]\d+)?\$)")


def _new_design(text, photos):
    """Katalogda umuman yo'q bo'lib qolgan dizaynni guruhdagi jadvaldan qayta yaratadi."""
    t = " ".join(text.split())
    m = re.match(r"^(.{3,30}?)\s+\1\s+No\.", t)
    if not m:
        return None
    title = m.group(1)
    key = "boden" if title.lower().startswith("boden") else ("lenon" if title.lower().startswith("lenon") else None)
    if not key or not store.section(key):
        return None
    did = store.add_design(key, title, photos=photos[:20], log_event=False, mark_new=False)
    d = store.designs[did]
    body = t[m.end():]
    for r in ROW.finditer(body):
        name = r.group(2)
        out = "Qolmadi" in name or "❌" in name
        name = re.sub(r"❌|Qolmadi|🆕|✅|Keldi", "", name).strip()
        name = re.sub(r"^(Kodi\s+)?Narxi\s+", "", name).strip()
        if not name:
            continue
        it = store.add_item(d, "Asosiy", name, r.group(3) or "", r.group(4).replace(",", "."), log_event=False)
        it["new_until"] = 0
        if out:
            it["in_stock"] = False
    return d


async def run(owner, upto=700, report=True):
    gid = store.settings.get("group_id")
    if not gid or not owner:
        return None
    me = bot.me.get("id")
    found = {}          # design_id -> [(mid, [file_ids])]
    albums = {}         # media_group_id -> {"photos": [...], "text": ""}
    copies, seen, created = [], 0, []
    for mid in range(1, upto + 1):
        try:
            m = await bot.call("forwardMessage", chat_id=owner, from_chat_id=gid, message_id=mid,
                               disable_notification=True)
        except TelegramError:
            continue
        except Exception as e:  # noqa
            log.info("forward %s: %s", mid, e)
            continue
        seen += 1
        copies.append(m["message_id"])
        ph, tx = [], []
        _photos(m, ph)
        _texts(m, tx)
        text = tx[0] if tx else ""
        g = m.get("media_group_id")
        if g:
            a = albums.setdefault(g, {"photos": [], "text": "", "mid": mid})
            a["photos"] += [p for p, _ in ph]
            a["text"] = a["text"] or text
            continue
        if ph:
            d = _match(text)
            if not d and mid > 400:
                d = _new_design(text, [p for p, _ in ph])
                if d:
                    created.append(html.escape(d["title"]) + f" ({sum(len(c['items']) for c in d['categories'])} ta mahsulot)")
            if d:
                found.setdefault(d["id"], []).append((mid, [p for p, _ in ph]))
        if len(copies) >= 50:
            await _cleanup(owner, copies)
            copies = []
    await _cleanup(owner, copies)
    for a in albums.values():
        d = _match(a["text"])
        if d and a["photos"]:
            found.setdefault(d["id"], []).append((a["mid"], a["photos"]))
    restored = []
    for did, posts in found.items():
        d = store.designs[did]
        _, photos = max(posts, key=lambda p: p[0])     # eng oxirgi post
        if len(photos) > len(d.get("photos") or []):
            d["photos"] = photos[:20]
            restored.append(f"{html.escape(d['title'])} — {len(photos)} ta rasm")
    if restored or created:
        store.save()
    log.info("guruhdan: %d xabar ko'rildi, %d dizaynga rasm qaytarildi", seen, len(restored))
    if report:
        no = [html.escape(d["title"]) for d in store.designs.values()
              if not d.get("deleted") and not d.get("photos")
              and (store.section(d["section"]) or {}).get("kind") != "photos"]
        msg = f"🖼 Guruhdagi {seen} ta postdan rasmlar qidirildi.\n"
        msg += (f"✅ {len(restored)} ta dizaynga rasm qaytarildi:\n" + "\n".join("• " + x for x in restored[:60])
                if restored else "Qaytariladigan rasm topilmadi.")
        if created:
            msg += "\n\n🆕 Katalogda yo'q bo'lib qolgan dizayn guruhdagi jadvaldan qayta yaratildi (tekshirib chiqing):\n" \
                   + "\n".join("• " + x for x in created)
        if no:
            msg += f"\n\n⚠️ Hali rasmi yo'q: {', '.join(no[:40])}" + (" …" if len(no) > 40 else "")
        await bot.send(owner, msg)
    return restored


async def _cleanup(owner, ids):
    if not ids:
        return
    try:
        await bot.call("deleteMessages", chat_id=owner, message_ids=ids)
    except Exception:  # noqa
        for i in ids:
            try:
                await bot.call("deleteMessage", chat_id=owner, message_id=i)
            except Exception:  # noqa
                pass
