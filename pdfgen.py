"""LENON — dizayn katalogi PDF (zamonaviy, «premium» uslub: qora + oltin muqova, krem rangli sahifalar).

Tuzilishi:
  1. Muqova — qora fon, logo, dizayn rasmi, nomi, qisqa ma'lumot va guruhlar ro'yxati
  2. Har bir guruh (Asosiy, Tarelka turk, …) — alohida sahifa, kartochkalar sahifani to'ldiradi
  3. Orqa muqova — buyurtma uchun agentlar va guruh manzili
"""
import io
from datetime import datetime

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

import config

W, H = 1240, 1754                # A4, 150 dpi
M = 80                           # chet
INK = (20, 18, 15)               # qora (muqova foni)
INK2 = (34, 30, 25)
CREAM = (250, 247, 240)          # sahifa foni
CARD = (255, 255, 255)
GOLD = (201, 155, 42)
GOLD_L = (231, 205, 140)
GOLD_D = (150, 112, 25)
TEXT = (28, 25, 21)
MUTED = (128, 118, 104)
RED = (200, 38, 38)
GREEN = (26, 140, 70)

_FONTS = {}


def _f(kind, size):
    """kind: serif / serif-b / sans / sans-m / sans-b / sans-l"""
    files = {"serif": ["Lora-SemiBold.ttf"], "serif-b": ["Lora-Bold.ttf"], "serif-m": ["Lora-Medium.ttf"],
             "sans": ["Poppins-Regular.ttf", "label.ttf"], "sans-m": ["Poppins-Medium.ttf", "label_bold.ttf"],
             "sans-b": ["Poppins-Bold.ttf", "label_bold.ttf"], "sans-l": ["Poppins-Light.ttf", "label.ttf"]}[kind]
    key = (kind, size)
    if key not in _FONTS:
        for n in files + ["label.ttf"]:
            for p in (config.ASSETS / n, "/usr/share/fonts/truetype/google-fonts/" + n,
                      "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"):
                try:
                    _FONTS[key] = ImageFont.truetype(str(p), size)
                    break
                except OSError:
                    pass
            if key in _FONTS:
                break
        else:
            _FONTS[key] = ImageFont.load_default(size)
    return _FONTS[key]


def _logo(width, alpha=1.0):
    lg = Image.open(config.ASSETS / "logo.png").convert("RGBA")
    lg = lg.resize((width, int(lg.height * width / lg.width)), Image.LANCZOS)
    if alpha < 1:
        lg.putalpha(lg.split()[3].point(lambda a: int(a * alpha)))
    return lg


def _fit(d, text, font, maxw):
    text = str(text or "")
    if d.textlength(text, font=font) <= maxw:
        return text
    while text and d.textlength(text + "…", font=font) > maxw:
        text = text[:-1]
    return text + "…"


def _spaced(d, xy, text, font, fill, spacing=6, anchor="l"):
    """Harflar orasi kengaytirilgan matn (sarlavhalar uchun)."""
    x, y = xy
    total = sum(d.textlength(ch, font=font) for ch in text) + spacing * (len(text) - 1)
    if anchor == "m":
        x -= total / 2
    elif anchor == "r":
        x -= total
    for ch in text:
        d.text((x, y), ch, font=font, fill=fill)
        x += d.textlength(ch, font=font) + spacing
    return total


def _rounded(img, radius):
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, img.width - 1, img.height - 1], radius=radius, fill=255)
    return mask


def _shadow(base, box, radius=22, blur=18, opacity=55, offset=8):
    x0, y0, x1, y1 = box
    pad = blur * 2
    sh = Image.new("L", (x1 - x0 + pad * 2, y1 - y0 + pad * 2), 0)
    ImageDraw.Draw(sh).rounded_rectangle([pad, pad + offset, pad + x1 - x0, pad + y1 - y0 + offset],
                                         radius=radius, fill=opacity)
    sh = sh.filter(ImageFilter.GaussianBlur(blur))
    base.paste((60, 45, 20), (x0 - pad, y0 - pad), sh)


def _photo_box(raw, w, h):
    """Rasm qirqilmaydi: to'liq joylanadi, bo'sh joy o'sha rasmning xira foni bilan to'ldiriladi."""
    src = Image.open(io.BytesIO(raw)).convert("RGB")
    bg = ImageOps.fit(src, (w, h), Image.LANCZOS).filter(ImageFilter.GaussianBlur(22))
    fg = src.copy()
    fg.thumbnail((w, h), Image.LANCZOS)
    bg.paste(fg, ((w - fg.width) // 2, (h - fg.height) // 2))
    return bg


def _badge(d, x, y, text, color, font, right=False):
    tw = d.textlength(text, font=font)
    w = tw + 26
    if right:
        x -= w
    d.rounded_rectangle([x, y, x + w, y + 34], radius=17, fill=color)
    d.text((x + 13, y + 17), text, font=font, fill="white", anchor="lm")


# ---------------------------------------------------------------- muqova
def _cover(design, section_title, cover, cats, total, toc, stamp):
    im = Image.new("RGB", (W, H), INK)
    d = ImageDraw.Draw(im)
    # nozik oltin ramka
    d.rectangle([40, 40, W - 40, H - 40], outline=GOLD_D, width=2)
    d.rectangle([52, 52, W - 52, H - 52], outline=(70, 56, 25), width=1)
    lg = _logo(330)
    im.paste(lg, ((W - lg.width) // 2, 110), lg)
    y = 110 + lg.height + 26
    _spaced(d, (W // 2, y), f"{section_title.upper()}  KOLLEKSIYASI", _f("sans-m", 22), GOLD_L, 8, "m")
    y += 70

    # dizayn rasmi
    if cover:
        try:
            c = Image.open(io.BytesIO(cover)).convert("RGB")
            c.thumbnail((W - 2 * M - 40, 640), Image.LANCZOS)
            x = (W - c.width) // 2
            _shadow(im, (x, y, x + c.width, y + c.height), radius=30, blur=26, opacity=160, offset=14)
            im.paste(c, (x, y), _rounded(c, 30))
            y += c.height + 60
        except Exception:  # noqa
            y += 40
    else:
        y += 120

    # nomi
    name = design["title"].replace("LENON ", "").replace("BODEN ", "")
    d.text((W // 2, y), name, font=_f("serif-b", 120), fill=GOLD, anchor="mt")
    y += 150
    d.line([(W // 2 - 90, y), (W // 2 + 90, y)], fill=GOLD, width=3)
    y += 30
    d.text((W // 2, y), f"{total} ta mahsulot   ·   {len(cats)} ta guruh   ·   {stamp}",
           font=_f("sans-l", 26), fill=(214, 204, 186), anchor="mt")
    y += 80

    # guruhlar (mundarija) — ikki ustunda
    f_n, f_p = _f("sans-m", 24), _f("sans-l", 22)
    colw = (W - 2 * M - 60) // 2
    rows = -(-len(toc) // 2)
    for i, (nm, n, pg) in enumerate(toc):
        col, r = divmod(i, rows)
        if y + r * 42 > H - 130:
            continue
        x = M + 30 + col * (colw + 60)
        yy = y + r * 42
        d.text((x, yy), _fit(d, nm, f_n, colw - 150), font=f_n, fill=(238, 230, 214))
        d.text((x + colw, yy + 2), f"{n} ta  ·  {pg}", font=f_p, fill=GOLD_L, anchor="ra")
        d.line([(x, yy + 35), (x + colw, yy + 35)], fill=(60, 50, 32), width=1)
    _spaced(d, (W // 2, H - 100), "@LENONDOKON", _f("sans-m", 20), GOLD_D, 6, "m")
    return im


# ---------------------------------------------------------------- guruh sahifasi
def _page_head(im, d, design_title, cat_name, count, part, page_no):
    d.rectangle([0, 0, W, 14], fill=GOLD)
    lg = _logo(130)
    im.paste(lg, (M, 46), lg)
    _spaced(d, (W - M, 58), design_title.upper(), _f("sans-m", 20), MUTED, 4, "r")
    y = 46 + lg.height + 40
    d.text((M, y), cat_name, font=_f("serif-b", 64), fill=TEXT)
    y += 86
    sub = f"{count} ta mahsulot" + (f"  ·  {part}-qism" if part else "")
    d.text((M, y), sub, font=_f("sans-l", 24), fill=MUTED)
    d.line([(M, y + 50), (M + 120, y + 50)], fill=GOLD, width=4)
    # pastki qism
    d.line([(M, H - 84), (W - M, H - 84)], fill=GOLD_L, width=1)
    _spaced(d, (M, H - 66), "LENON", _f("sans-b", 18), GOLD_D, 5)
    d.text((M + 90, H - 66), "·  @lenondokon", font=_f("sans", 18), fill=MUTED)
    d.text((W - M, H - 66), f"{page_no:02d}", font=_f("sans-b", 18), fill=GOLD_D, anchor="ra")
    return y + 90


def _text_h(cw):
    k = max(0.6, min(1.6, cw / 300))
    return int(30 * k) + int(24 * k) + int(46 * k) + 34


def _best_grid(n, aw, ah, gap):
    best = None
    for cols in range(1, 6):
        rows = -(-n // cols)
        cw = (aw - gap * (cols - 1)) // cols
        if cw < 150:
            break
        ph = min(int(cw * 1.2), (ah - gap * (rows - 1)) // rows - _text_h(cw))
        if ph < 110:
            continue
        score = min(cw, ph) * (1 if ph >= cw * 0.75 else 0.85)
        if best is None or score > best[0]:
            best = (score, cols, cw, ph)
    return best


def _card(im, d, x, y, cw, ph, it, raw, st):
    k = max(0.6, min(1.6, cw / 300))
    ch = ph + _text_h(cw)
    _shadow(im, (x, y, x + cw, y + ch), radius=22, blur=14, opacity=40, offset=6)
    d.rounded_rectangle([x, y, x + cw, y + ch], radius=22, fill=CARD)
    if raw:
        try:
            p = _photo_box(raw, cw, ph)
            # faqat yuqori burchaklar yumaloq
            mask = Image.new("L", p.size, 0)
            md = ImageDraw.Draw(mask)
            md.rounded_rectangle([0, 0, cw - 1, ph + 30], radius=22, fill=255)
            im.paste(p, (x, y), mask)
        except Exception:  # noqa
            raw = None
    if not raw:
        d.rounded_rectangle([x, y, x + cw, y + ph + 22], radius=22, fill=(242, 236, 222))
        d.rectangle([x, y + ph, x + cw, y + ph + 22], fill=CARD)
        lg = _logo(max(60, int(cw * 0.45)), 0.35)
        im.paste(lg, (x + (cw - lg.width) // 2, y + (ph - lg.height) // 2), lg)
    fb = _f("sans-b", max(14, int(15 * k)))
    if st == "qolmadi":
        veil = Image.new("RGBA", (cw, ph), (255, 255, 255, 120))
        im.paste(veil, (x, y), veil)
        _badge(d, x + cw - 14, y + 14, "QOLMADI", RED, fb, right=True)
    elif st == "keldi":
        _badge(d, x + cw - 14, y + 14, "KELDI", GREEN, fb, right=True)
    if st == "new":
        _badge(d, x + 14, y + 14, "NEW", RED, fb)

    pad = int(18 * k)
    ty = y + ph + int(14 * k)
    f_name = _f("sans-b", int(25 * k))
    f_meta = _f("sans", int(18 * k))
    d.text((x + pad, ty), _fit(d, it["name"], f_name, cw - 2 * pad), font=f_name, fill=TEXT)
    ty += int(32 * k)
    meta = "  ·  ".join(v for v in (it.get("code", ""), it.get("size", "")) if v)
    d.text((x + pad, ty), _fit(d, meta, f_meta, cw - 2 * pad), font=f_meta, fill=MUTED)
    # narx — oltin «tabletka»
    price = str(it.get("price", "") or "")
    if price:
        f_pr = _f("sans-b", int(24 * k))
        tw = d.textlength(price, font=f_pr)
        bh = int(42 * k)
        by = y + ch - bh - int(14 * k)
        color = (235, 225, 225) if st == "qolmadi" else GOLD
        tcol = RED if st == "qolmadi" else "white"
        d.rounded_rectangle([x + pad, by, x + pad + tw + int(30 * k), by + bh], radius=bh // 2, fill=color)
        d.text((x + pad + int(15 * k), by + bh // 2), price, font=f_pr, fill=tcol, anchor="lm")


# ---------------------------------------------------------------- orqa muqova
def _back(agents):
    im = Image.new("RGB", (W, H), INK)
    d = ImageDraw.Draw(im)
    d.rectangle([40, 40, W - 40, H - 40], outline=GOLD_D, width=2)
    lg = _logo(420)
    y = 520
    im.paste(lg, ((W - lg.width) // 2, y), lg)
    y += lg.height + 70
    d.text((W // 2, y), "Buyurtma berish uchun", font=_f("serif", 52), fill=GOLD, anchor="mt")
    y += 100
    for name, user in agents:
        if not user:
            continue
        d.text((W // 2, y), f"{name}   ·   @{user}", font=_f("sans-m", 32), fill=(238, 230, 214), anchor="mt")
        y += 62
    y += 50
    d.line([(W // 2 - 90, y), (W // 2 + 90, y)], fill=GOLD, width=2)
    y += 40
    d.text((W // 2, y), "Telegram guruhimiz:  @lenondokon", font=_f("sans-l", 28), fill=GOLD_L, anchor="mt")
    return im


def build(design: dict, section_title: str, photos: dict, cover: bytes | None = None,
          state_of=None) -> bytes:
    """design — {"title", "categories", "agents": [(ism, username)]}; photos — {item_id: jpeg}"""
    state_of = state_of or (lambda it: "")
    stamp = datetime.now(config.TZ).strftime("%d.%m.%Y")
    cats = [c for c in design["categories"] if c["items"]]
    total = sum(len(c["items"]) for c in cats)
    per_page = 16

    toc, pno = [], 2
    for c in cats:
        toc.append((c["name"], len(c["items"]), f"{pno:02d}"))
        pno += -(-len(c["items"]) // per_page)

    pages = [_cover(design, section_title, cover, cats, total, toc, stamp)]
    gap = 28
    page_no = 1
    for c in cats:
        items = c["items"]
        parts = -(-len(items) // per_page)
        for pi in range(parts):
            chunk = items[pi * per_page:(pi + 1) * per_page]
            page_no += 1
            im = Image.new("RGB", (W, H), CREAM)
            d = ImageDraw.Draw(im)
            y = _page_head(im, d, design["title"], c["name"], len(items), pi + 1 if parts > 1 else 0, page_no)
            aw, ah = W - 2 * M, H - 120 - y
            g = _best_grid(len(chunk), aw, ah, gap) or (0, 4, (aw - 3 * gap) // 4, (aw - 3 * gap) // 4)
            _, cols, cw, ph = g
            card_h = ph + _text_h(cw)
            rows = -(-len(chunk) // cols)
            used = rows * card_h + (rows - 1) * gap
            y0 = y + max(0, (ah - used) // 2)
            for i, it in enumerate(chunk):
                r, col = divmod(i, cols)
                n_in_row = min(cols, len(chunk) - r * cols)
                x0 = M + (aw - (n_in_row * cw + (n_in_row - 1) * gap)) // 2
                _card(im, d, x0 + col * (cw + gap), y0 + r * (card_h + gap), cw, ph, it,
                      photos.get(it["id"]), state_of(it))
            pages.append(im)
    pages.append(_back(design.get("agents") or []))

    buf = io.BytesIO()
    pages[0].save(buf, "PDF", resolution=150, save_all=True, append_images=pages[1:],
                  title=f"LENON katalog — {design['title']}", author="LENON")
    return buf.getvalue()
