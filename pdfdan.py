"""Bot yasagan PDF katalogdan mahsulot rasmlarini qaytarib olish.

Bot PDF'ni o'zi yasagan (pdfgen.py): har sahifa — bitta rasm, kartochkalar joyi faqat
mahsulotlar soniga bog'liq. Shuning uchun har bir mahsulot rasmining joyini aniq hisoblab,
sahifadan qirqib olamiz va katalogdagi o'sha mahsulotga qaytaramiz."""
import asyncio
import html
import io
import logging
import re

from PIL import Image, ImageFilter

import pdfgen
from store import store
from tg import bot

log = logging.getLogger("pdfdan")
W, H, M = pdfgen.W, pdfgen.H, pdfgen.M
PER_PAGE, GAP, HEAD_Y = 16, 28, 330


def pages(pdf: bytes) -> list:
    """PDF ichidagi sahifa rasmlari (JPEG) tartib bilan."""
    out = []
    for m in re.finditer(rb"/Filter\s*/DCTDecode\s*/Length\s+(\d+)\s*>>\s*stream\r?\n", pdf):
        n = int(m.group(1))
        out.append(pdf[m.end():m.end() + n])
    return out


def _norm(s):
    return re.sub(r"[^0-9a-z]+", "", (s or "").lower())


def find_design(name: str, caption: str = ""):
    keys = [_norm(caption.split("—")[0]), _norm(re.sub(r"\.pdf$", "", name or "", flags=re.I))]
    for d in store.designs.values():
        if d.get("deleted"):
            continue
        n = _norm(d["title"])
        if n and n in keys:
            return d
    return None


def _trim(box: Image.Image) -> Image.Image:
    """Xira fon ichidagi aniq rasmni ajratadi. Aniq rasm markazda, bir tomoni to'liq;
    uning atrofidagi xira fon chiziqlari deyarli tekis bo'ladi — chetdan ichkariga qarab
    birinchi «tekis bo'lmagan» chiziqni topamiz (ikki tomondan, simmetrik)."""
    import numpy as np
    a = np.asarray(box.convert("L"), dtype=np.float32)
    h, w = a.shape
    m = 26                                              # yumaloq burchaklar va belgilarni hisobga olmaymiz
    row_g = np.percentile(np.abs(np.diff(a[:, m:w - m], axis=1)), 60, axis=1)   # qator keskinligi
    col_g = np.percentile(np.abs(np.diff(a[m:h - m, :], axis=0)), 60, axis=0)   # ustun keskinligi

    def edge(g):
        n = len(g)
        core = g[n // 3: 2 * n // 3]
        t = max(0.8, 0.3 * float(np.median(core)))
        lo = next((i for i in range(n) if g[i] > t), 0)
        hi = next((i for i in range(n) if g[n - 1 - i] > t), 0)
        return min(lo, hi)
    top, left = edge(row_g), edge(col_g)
    if top >= left and top > 3 and h - 2 * top > 30:
        return box.crop((0, top, w, h - top))
    if left > 3 and w - 2 * left > 30:
        return box.crop((left, 0, w - left, h))
    return box


def _unveil(im: Image.Image) -> Image.Image:
    """«QOLMADI» kartochkasidagi oq pardani (alpha 120) olib tashlaydi."""
    a = 120 / 255
    return im.point(lambda v: max(0, min(255, int((v - 255 * a) / (1 - a)))))


def _layout(n):
    aw, ah = W - 2 * M, H - 120 - HEAD_Y
    g = pdfgen._best_grid(n, aw, ah, GAP) or (0, 4, (aw - 3 * GAP) // 4, (aw - 3 * GAP) // 4)
    _, cols, cw, ph = g
    card_h = ph + pdfgen._text_h(cw)
    rows = -(-n // cols)
    used = rows * card_h + (rows - 1) * GAP
    y0 = HEAD_Y + max(0, (ah - used) // 2)
    out = []
    for i in range(n):
        r, col = divmod(i, cols)
        n_in_row = min(cols, n - r * cols)
        x0 = M + (aw - (n_in_row * cw + (n_in_row - 1) * GAP)) // 2
        out.append((x0 + col * (cw + GAP), y0 + r * (card_h + GAP), cw, ph, card_h))
    return out


def _detect_n(im) -> int:
    """Sahifadagi kartochkalar soni: kartochka chegarasi (kulrang chiziq) to'g'ri joyda bo'lgan eng katta n."""
    g = im.convert("L").load()
    best = 0
    for n in range(1, PER_PAGE + 1):
        ok = True
        for x, y, cw, ph, ch in _layout(n):
            yy = y + ph + (ch - ph) // 2
            l = min(g[x + dx, yy] for dx in range(0, 3))
            r = min(g[x + cw - dx, yy] for dx in range(0, 3))
            inside = g[x + cw // 2, y + ch - 4]
            if not (l < 246 and r < 246 and inside > 240):
                ok = False
                break
        if ok:
            best = n
    return best


def _text_img(text, font, w, h):
    from PIL import ImageDraw
    im = Image.new("L", (w, h), 255)
    dr = ImageDraw.Draw(im)
    dr.text((0, 0), pdfgen._fit(dr, text, font, w), font=font, fill=0)
    return im


def _diff(a, b) -> float:
    import numpy as np
    return float(np.abs(np.asarray(a, dtype=np.float32) - np.asarray(b, dtype=np.float32)).mean())


def extract(pdf: bytes, design: dict, state_of) -> tuple[dict, bytes | None, str]:
    """Natija: ({item_id: jpeg}, muqova_jpeg yoki None, izoh).
    Har bir kartochka nomi va kodi rasm sifatida solishtiriladi — mahsulotlar tartibi o'zgargan bo'lsa ham to'g'ri topiladi."""
    jp = pages(pdf)
    if len(jp) < 3:
        return {}, None, "PDF'da mahsulot sahifalari topilmadi"
    cats = [c for c in design["categories"] if c["items"]]
    out, scores = {}, {}
    cover = None
    try:
        im = Image.open(io.BytesIO(jp[0])).convert("RGB")
        side = 640
        x, y = W - M - side + 20, 620
        c = im.crop((x + 6, y + 6, x + side - 6, y + side - 6))
        sm = c.resize((64, 64))
        plain = sum(1 for p in sm.getdata()
                    if min(abs(p[0] - q[0]) + abs(p[1] - q[1]) + abs(p[2] - q[2]) for q in
                           ((255, 255, 255), pdfgen.YEL, pdfgen.YEL2, pdfgen.DARK)) < 30)
        if plain < 64 * 64 * 0.6:
            buf = io.BytesIO()
            c.save(buf, "JPEG", quality=92)
            cover = buf.getvalue()
    except Exception:  # noqa
        pass
    f_cat = pdfgen._f("sans-b", 66)
    for page in jp[1:-1]:
        im = Image.open(io.BytesIO(page)).convert("RGB")
        gray = im.convert("L")
        # qaysi guruh sahifasi — sarlavhani solishtiramiz (oq yozuv sariq fonda: teskari qilib)
        head = gray.crop((M, 92, M + 900, 92 + 80)).point(lambda v: 255 - v if v > 200 else 255)
        cand_cats = sorted(cats, key=lambda c: _diff(head, _text_img(c["name"], f_cat, 900, 80)))
        n = _detect_n(im)
        for x, y, cw, ph, ch in _layout(n):
            k = max(0.6, min(1.6, cw / 300))
            pad = int(18 * k)
            ty = y + ph + int(14 * k)
            f_name = pdfgen._f("sans-b", int(25 * k))
            f_meta = pdfgen._f("sans", int(18 * k))
            tw = cw - 2 * pad
            nm = gray.crop((x + pad, ty, x + pad + tw, ty + int(32 * k))).point(lambda v: 0 if v < 140 else 255)
            mt = gray.crop((x + pad, ty + int(32 * k), x + pad + tw, ty + int(32 * k) + int(26 * k))).point(
                lambda v: 0 if v < 200 else 255)
            best = None
            for c in cand_cats[:2]:
                for it in c["items"]:
                    a = _diff(nm, _text_img(it["name"], f_name, tw, int(32 * k)).point(lambda v: 0 if v < 140 else 255))
                    meta = "  ·  ".join(v for v in (it.get("code", ""), it.get("size", "")) if v)
                    b2 = _diff(mt, _text_img(meta, f_meta, tw, int(26 * k)).point(lambda v: 0 if v < 200 else 255))
                    sc = a + 0.5 * b2 + (0 if c is cand_cats[0] else 8)
                    if best is None or sc < best[0]:
                        best = (sc, it)
            if not best or best[0] > 40:
                continue
            it = best[1]
            box = im.crop((x + 2, y + 2, x + cw - 2, y + ph))
            px = box.resize((24, 24)).getdata()
            if sum(1 for p in px if abs(p[0] - 242) < 6 and abs(p[1] - 236) < 6 and abs(p[2] - 222) < 6) > 400:
                continue          # rasmsiz kartochka
            if it["id"] in scores and scores[it["id"]] <= best[0]:
                continue
            # «QOLMADI» belgisi bo'lgan kartochka — oq pardani olib tashlaymiz
            if gray.getpixel((x + cw - 30, y + 30)) < 150 and im.getpixel((x + cw - 30, y + 30))[0] > 150:
                box = _unveil(box)
            box = _trim(box)
            buf = io.BytesIO()
            box.save(buf, "JPEG", quality=92)
            out[it["id"]] = buf.getvalue()
            scores[it["id"]] = best[0]
    return out, cover, ""


async def restore(uid, pdf: bytes, name: str, caption: str = "", overwrite=False):
    import publisher
    d = find_design(name, caption)
    if not d:
        await bot.send(uid, f"❓ «{html.escape(name)}» qaysi dizaynga tegishli ekanini topa olmadim.")
        return
    photos, cover, err = await asyncio.get_event_loop().run_in_executor(
        None, extract, pdf, d, publisher.item_state)
    if err:
        await bot.send(uid, f"⚠️ {html.escape(d['title'])}: {html.escape(err)}. Rasmlarni qaytarib bo'lmadi.")
        return
    added = 0
    items = {it["id"]: it for c in d["categories"] for it in c["items"]}
    for iid, jpg in photos.items():
        it = items[iid]
        if it.get("photo") and not overwrite:
            continue
        m = await bot.call("sendPhoto", files={"photo": ("rasm.jpg", jpg, "image/jpeg")},
                           chat_id=str(uid), disable_notification="true")
        it["photo"] = m["photo"][-1]["file_id"]
        it["label_v"] = 2
        await bot.safe_delete(uid, m["message_id"])
        added += 1
        await asyncio.sleep(0.15)
    cov = False
    if cover and not d.get("photos"):
        m = await bot.call("sendPhoto", files={"photo": ("muqova.jpg", cover, "image/jpeg")},
                           chat_id=str(uid), disable_notification="true")
        d["photos"] = [m["photo"][-1]["file_id"]]
        await bot.safe_delete(uid, m["message_id"])
        cov = True
    store.save()
    total = len(items)
    await bot.send(uid, f"✅ <b>{html.escape(d['title'])}</b>: {added} ta mahsulot rasmi qaytarildi"
                        + (" + dizayn rasmi" if cov else "")
                        + f" (jami {total} ta mahsulot, PDF'da rasmi bori: {len(photos)})."
                        + ("\nRasmi oldindan bor mahsulotlarga tegilmadi." if not overwrite and len(photos) > added else ""))
