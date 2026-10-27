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


# ---------------------------------------------------------------- to'lqin shakllar
YEL = (232, 172, 30)      # yorqin oltin-sariq (asosiy rang)
YEL2 = (246, 205, 96)     # och sariq (ikkinchi to'lqin)
DARK = (33, 33, 36)       # to'q kulrang-qora


def _bez(p0, p1, p2, p3, n=60):
    pts = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        pts.append((u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                    u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
    return pts


def _shape(im, segments, close_pts, color, alpha=255):
    """Silliq (2x katta chizilib kichraytirilgan) to'lqin shakl. segments — bezye egri chiziqlar ro'yxati."""
    S = 2
    mask = Image.new("L", (W * S, H * S), 0)
    pts = []
    for seg in segments:
        pts += _bez(*seg)
    pts += close_pts
    ImageDraw.Draw(mask).polygon([(x * S, y * S) for x, y in pts], fill=alpha)
    mask = mask.resize((W, H), Image.LANCZOS)
    im.paste(color, (0, 0), mask)


def _cover(design, section_title, cover, cats, total, toc, stamp):
    im = Image.new("RGB", (W, H), "white")
    # pastdan ko'tarilgan katta oltin to'lqin
    _shape(im, [((0, 1040), (380, 900), (700, 1250), (W, 1080))], [(W, H), (0, H)], YEL2)
    _shape(im, [((0, 1130), (420, 1000), (760, 1330), (W, 1190))], [(W, H), (0, H)], YEL)
    # tepada o'ng burchakdagi to'lqin
    _shape(im, [((620, 0), (760, 160), (1000, 60), (W, 250))], [(W, 0)], YEL)
    _shape(im, [((820, 0), (930, 110), (1100, 40), (W, 140))], [(W, 0)], DARK)
    d = ImageDraw.Draw(im)
    lg = _logo(260)
    im.paste(lg, (M, 90), lg)
    # sarlavha
    y = 300
    d.text((M, y), section_title.capitalize() + " kolleksiyasi", font=_f("sans-m", 40), fill=DARK)
    y += 60
    d.text((M - 4, y), "Katalog", font=_f("sans-b", 150), fill=YEL)
    y += 190
    d.text((M, y), f"{total} ta mahsulot  ·  {len(cats)} ta guruh  ·  {stamp}", font=_f("sans", 26), fill=MUTED)

    # dizayn rasmi — oq ramkali dumaloq burchakli kartochka, to'lqin ustida
    if cover:
        try:
            c = Image.open(io.BytesIO(cover)).convert("RGB")
            side = 640
            c = ImageOps.fit(c, (side, side), Image.LANCZOS)
            x, yy = W - M - side + 20, 620
            _shadow(im, (x - 14, yy - 14, x + side + 14, yy + side + 14), radius=44, blur=30, opacity=120, offset=18)
            d.rounded_rectangle([x - 14, yy - 14, x + side + 14, yy + side + 14], radius=44, fill="white")
            im.paste(c, (x, yy), _rounded(c, 34))
        except Exception:  # noqa
            pass
    # dizayn nomi to'lqin ustida, oq rangda
    name = design["title"].replace("LENON ", "").replace("BODEN ", "")
    d.text((M, 1340), name, font=_f("sans-b", 130), fill="white")
    d.text((M + 4, 1490), "Dizayn", font=_f("sans-m", 34), fill=DARK)
    # guruhlar ro'yxati (qisqa)
    f_t = _f("sans-m", 23)
    names = [n for n, _, _ in toc]
    line, lines = "", []
    for nm in names:
        add = (line + "  ·  " if line else "") + nm
        if d.textlength(add, font=f_t) > W - 2 * M:
            lines.append(line)
            line = nm
        else:
            line = add
    if line:
        lines.append(line)
    yy = 1560
    for ln in lines[:3]:
        d.text((M, yy), ln, font=f_t, fill=DARK)
        yy += 36
    d.text((W - M, H - 70), "@lenondokon", font=_f("sans-b", 24), fill=DARK, anchor="rs")
    return im


# ---------------------------------------------------------------- guruh sahifasi
def _page_head(im, d, design_title, cat_name, count, part, page_no):
    # tepadagi to'lqinli sarlavha tasmasi
    _shape(im, [((0, 300), (400, 380), (760, 160), (W, 250))], [(W, 0), (0, 0)], YEL2)
    _shape(im, [((0, 270), (380, 330), (720, 130), (W, 200))], [(W, 0), (0, 0)], YEL)
    _shape(im, [((W - 260, 0), (W - 200, 60), (W - 80, 30), (W, 110))], [(W, 0)], DARK)
    d = ImageDraw.Draw(im)
    d.text((M, 56), design_title.upper(), font=_f("sans-m", 22), fill=DARK)
    d.text((M, 92), cat_name, font=_f("sans-b", 66), fill="white")
    sub = f"{count} ta mahsulot" + (f"  ·  {part}-qism" if part else "")
    d.text((M, 180), sub, font=_f("sans-m", 24), fill=DARK)
    # pastki o'ng burchak — to'lqin va sahifa raqami
    _shape(im, [((W - 420, H), (W - 300, H - 60), (W - 160, H - 150), (W, H - 120))], [(W, H)], YEL)
    d = ImageDraw.Draw(im)
    d.text((W - 60, H - 46), f"{page_no:02d}", font=_f("sans-b", 34), fill="white", anchor="rs")
    d.text((M, H - 50), "LENON  ·  @lenondokon", font=_f("sans-m", 18), fill=MUTED, anchor="ls")
    return 330


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
    d.rounded_rectangle([x, y, x + cw, y + ch], radius=22, fill=CARD, outline=(236, 236, 236), width=2)
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
        color = (245, 225, 225) if st == "qolmadi" else YEL
        tcol = RED if st == "qolmadi" else DARK
        d.rounded_rectangle([x + pad, by, x + pad + tw + int(30 * k), by + bh], radius=bh // 2, fill=color)
        d.text((x + pad + int(15 * k), by + bh // 2), price, font=f_pr, fill=tcol, anchor="lm")


# ---------------------------------------------------------------- orqa muqova
def _back(agents):
    im = Image.new("RGB", (W, H), DARK)
    _shape(im, [((0, 1180), (400, 1040), (760, 1380), (W, 1220))], [(W, H), (0, H)], YEL2)
    _shape(im, [((0, 1270), (420, 1140), (780, 1460), (W, 1320))], [(W, H), (0, H)], YEL)
    _shape(im, [((560, 0), (720, 180), (980, 60), (W, 280))], [(W, 0)], YEL)
    d = ImageDraw.Draw(im)
    lg = _logo(380)
    im.paste(lg, (M, 300), lg)
    y = 300 + lg.height + 80
    d.text((M, y), "Buyurtma", font=_f("sans-b", 96), fill="white")
    y += 115
    d.text((M, y), "berish uchun", font=_f("sans-l", 60), fill=YEL)
    y += 120
    for name, user in agents:
        if not user:
            continue
        d.ellipse([M, y + 14, M + 16, y + 30], fill=YEL)
        d.text((M + 36, y), f"{name}", font=_f("sans-b", 34), fill="white")
        d.text((M + 330, y + 2), f"@{user}", font=_f("sans", 32), fill=(220, 220, 220))
        y += 66
    d.text((M, 1420), "Telegram guruhimiz", font=_f("sans-m", 30), fill=DARK)
    d.text((M, 1460), "@lenondokon", font=_f("sans-b", 64), fill="white")
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

    pages = [_jpeg(_cover(design, section_title, cover, cats, total, toc, stamp))]
    gap = 28
    page_no = 1
    for c in cats:
        items = c["items"]
        parts = -(-len(items) // per_page)
        for pi in range(parts):
            chunk = items[pi * per_page:(pi + 1) * per_page]
            page_no += 1
            im = Image.new("RGB", (W, H), "white")
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
            pages.append(_jpeg(im))
            del im, d
    pages.append(_jpeg(_back(design.get("agents") or [])))

    return _write_pdf(pages, f"LENON katalog — {design['title']}")


# ---------------------------------------------------------------- yengil PDF yozuvchi
def _jpeg(im) -> bytes:
    """Sahifani darhol JPEG ga siqamiz — xotirada katta rasmlar to'planmaydi (bepul server uchun muhim)."""
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=88, optimize=True)
    return buf.getvalue()


def _write_pdf(jpegs: list, title: str) -> bytes:
    """JPEG sahifalardan PDF yasaydi (har sahifa A4)."""
    pw, ph = 595.28, 841.89  # A4, nuqtalarda
    objs = []

    def add(b):
        objs.append(b)
        return len(objs)

    catalog = add(b"")
    pages_id = add(b"")
    kids = []
    for jp in jpegs:
        img_id = add(b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB "
                     b"/BitsPerComponent 8 /Filter /DCTDecode /Length %d >>\nstream\n" % (W, H, len(jp))
                     + jp + b"\nendstream")
        content = b"q %.2f 0 0 %.2f 0 0 cm /Im0 Do Q" % (pw, ph)
        c_id = add(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
        pg = add(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %.2f %.2f] /Resources << /XObject << /Im0 %d 0 R >> >> "
                 b"/Contents %d 0 R >>" % (pages_id, pw, ph, img_id, c_id))
        kids.append(pg)
    objs[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id
    objs[pages_id - 1] = (b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % k for k in kids)
                          + b"] /Count %d >>" % len(kids))
    t = title.encode("utf-16-be")
    info = add(b"<< /Title <FEFF" + t.hex().upper().encode() + b"> /Producer (LENON katalog bot) >>")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(out.tell())
        out.write(b"%d 0 obj\n" % i + o + b"\nendobj\n")
    x = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for o in offs:
        out.write(b"%010d 00000 n \n" % o)
    out.write(b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
              % (len(objs) + 1, catalog, info, x))
    return out.getvalue()
