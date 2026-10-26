"""Dizayn katalogini chiroyli PDF qilib tayyorlash (oq + oltin uslubda, A4)."""
import io
import time
from datetime import datetime

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

import config

W, H = 1240, 1754          # A4, 150 dpi
M = 70                     # chet
GOLD = (196, 150, 30)
GOLD_L = (232, 214, 160)
DARK = (34, 30, 24)
GREY = (120, 112, 100)
RED = (205, 40, 40)
GREEN = (30, 150, 70)
BG_CARD = (250, 247, 240)


def _f(size, bold=False):
    names = (["label_bold.ttf", "LiberationSans-Bold.ttf", "DejaVuSans-Bold.ttf"] if bold
             else ["label.ttf", "LiberationSans-Regular.ttf", "DejaVuSans.ttf"])
    for n in names:
        for p in (config.ASSETS / n, "/usr/share/fonts/truetype/liberation/" + n, "/usr/share/fonts/truetype/dejavu/" + n):
            try:
                return ImageFont.truetype(str(p), size)
            except OSError:
                pass
    return ImageFont.load_default(size)


def _logo(width):
    lg = Image.open(config.ASSETS / "logo.png").convert("RGBA")
    return lg.resize((width, int(lg.height * width / lg.width)), Image.LANCZOS)


def _fit_text(d, text, font, maxw):
    if d.textlength(text, font=font) <= maxw:
        return text
    while text and d.textlength(text + "…", font=font) > maxw:
        text = text[:-1]
    return text + "…"


def _badge(d, x, y, text, color, font):
    tw = d.textlength(text, font=font)
    d.rounded_rectangle([x, y, x + tw + 24, y + 32], radius=16, fill=color)
    d.text((x + 12, y + 16), text, font=font, fill="white", anchor="lm")
    return tw + 28


def _new_page(title, subtitle, page_no):
    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    if page_no > 1:  # keyingi sahifalarda ixcham sarlavha
        lg = _logo(150)
        im.paste(lg, (M, 40), lg)
        d.text((W - M, 40 + lg.height // 2), title, font=_f(30, True), fill=DARK, anchor="rm")
        y = 40 + lg.height + 14
        d.line([(M, y), (W - M, y)], fill=GOLD, width=2)
        d.line([(M, H - 80), (W - M, H - 80)], fill=GOLD_L, width=2)
        d.text((M, H - 52), "LENON · @lenondokon", font=_f(22), fill=GREY, anchor="lm")
        d.text((W - M, H - 52), f"{page_no}-sahifa", font=_f(22), fill=GREY, anchor="rm")
        return im, d, y + 26
    lg = _logo(300)
    im.paste(lg, ((W - lg.width) // 2, 50), lg)
    y = 50 + lg.height + 22
    d.text((W // 2, y), title, font=_f(46, True), fill=DARK, anchor="mt")
    y += 60
    if subtitle:
        d.text((W // 2, y), subtitle, font=_f(24), fill=GREY, anchor="mt")
        y += 38
    d.line([(W // 2 - 160, y + 6), (W // 2 + 160, y + 6)], fill=GOLD, width=3)
    d.ellipse([W // 2 - 7, y - 1, W // 2 + 7, y + 13], fill=GOLD)
    # pastki qism
    d.line([(M, H - 80), (W - M, H - 80)], fill=GOLD_L, width=2)
    d.text((M, H - 52), "LENON · @lenondokon", font=_f(22), fill=GREY, anchor="lm")
    d.text((W - M, H - 52), f"{page_no}-sahifa", font=_f(22), fill=GREY, anchor="rm")
    return im, d, y + 40


def _card(im, d, x, y, cw, card_h, ph_h, it, raw, st):
    """Bitta mahsulot kartochkasi (o'lcham kartaga qarab moslashadi)."""
    k = cw / 260.0
    f_name, f_code, f_price, f_badge = _f(int(23 * k ** 0.6), True), _f(int(18 * k ** 0.6)), \
        _f(int(24 * k ** 0.6), True), _f(int(16 * k ** 0.5), True)
    pad = max(6, int(8 * k ** 0.5))
    d.rounded_rectangle([x, y, x + cw, y + card_h], radius=20, fill=BG_CARD, outline=GOLD_L, width=2)
    pw, ph = cw - 2 * pad, ph_h - 2 * pad
    if raw:
        try:
            src = Image.open(io.BytesIO(raw)).convert("RGB")
            # rasm qirqilmaydi: butunligicha joylanadi, bo'sh joy o'sha rasmning xira foni bilan to'ldiriladi
            p = ImageOps.fit(src, (pw, ph), Image.LANCZOS).filter(ImageFilter.GaussianBlur(18))
            fg = src.copy()
            fg.thumbnail((pw, ph), Image.LANCZOS)
            p.paste(fg, ((pw - fg.width) // 2, (ph - fg.height) // 2))
            mask = Image.new("L", p.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, p.width, p.height], radius=14, fill=255)
            im.paste(p, (x + pad, y + pad), mask)
        except Exception:  # noqa
            raw = None
    if not raw:
        d.rounded_rectangle([x + pad, y + pad, x + pad + pw, y + pad + ph], radius=14, fill=(240, 235, 222))
        lg = _logo(max(40, int(min(pw, ph) * 0.55)))
        lg.putalpha(lg.split()[3].point(lambda a: int(a * 0.35)))
        im.paste(lg, (x + (cw - lg.width) // 2, y + (ph_h - lg.height) // 2), lg)
    if st == "qolmadi":
        veil = Image.new("RGBA", (pw, ph), (255, 255, 255, 110))
        im.paste(veil, (x + pad, y + pad), veil)
        bw = d.textlength("QOLMADI", font=f_badge) + 24
        _badge(d, x + cw - pad - 6 - bw, y + pad + 6, "QOLMADI", RED, f_badge)
    elif st == "keldi":
        bw = d.textlength("KELDI", font=f_badge) + 24
        _badge(d, x + cw - pad - 6 - bw, y + pad + 6, "KELDI", GREEN, f_badge)
    if st == "new":
        _badge(d, x + pad + 6, y + pad + 6, "NEW", RED, f_badge)
    lh = f_name.size + 8
    ty = y + ph_h + 4
    d.text((x + 14, ty), _fit_text(d, it["name"], f_name, cw - 28), font=f_name, fill=DARK)
    meta = " · ".join(v for v in (it.get("code", ""), it.get("size", "")) if v)
    d.text((x + 14, ty + lh), _fit_text(d, meta, f_code, cw - 28), font=f_code, fill=GREY)
    d.text((x + 14, ty + lh + f_code.size + 10), it.get("price", ""), font=f_price,
           fill=RED if st == "qolmadi" else GOLD)


def _text_h(cw):
    k = cw / 260.0
    return int((23 * k ** 0.6 + 8) + (18 * k ** 0.6 + 10) + 24 * k ** 0.6 + 22)


def _best_grid(n, aw, ah, gap):
    """n ta kartani aw×ah maydonga eng katta qilib joylash: (ustunlar, karta eni, rasm balandligi)."""
    best = None
    for cols in range(1, 6):
        rows = -(-n // cols)
        cw = (aw - gap * (cols - 1)) // cols
        if cw < 140:
            break
        ph = min(int(cw * 1.3), (ah - gap * (rows - 1)) // rows - _text_h(cw))
        if ph < 100:
            continue
        # rasm kvadratga yaqin bo'lsin (yon tomonlari ozgina qirqiladi), sahifa iloji boricha to'lsin
        score = min(cw, ph) * (1 if ph >= cw * 0.75 else 0.85)
        if best is None or score > best[0]:
            best = (score, cols, cw, ph)
    return best


def build(design: dict, section_title: str, photos: dict, cover: bytes | None = None,
          state_of=None) -> bytes:
    """Har bir guruh (masalan «Tar turk») — alohida sahifa, kartochkalar butun sahifani egallaydi."""
    title = design["title"]
    stamp = datetime.now(config.TZ).strftime("%d.%m.%Y")
    cats = [c for c in design["categories"] if c["items"]]
    total = sum(len(c["items"]) for c in cats)
    pages = []

    # 1-sahifa: muqova — dizayn rasmi va mundarija
    im, d, y = _new_page(title, f"{section_title} · {total} ta mahsulot · {stamp}", 1)
    if cover:
        try:
            c = Image.open(io.BytesIO(cover)).convert("RGB")
            c.thumbnail((W - 2 * M, 820), Image.LANCZOS)   # butun rasm ko'rinsin (logo qirqilmasin)
            mask = Image.new("L", c.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, c.width, c.height], radius=28, fill=255)
            im.paste(c, ((W - c.width) // 2, y), mask)
            y += c.height + 50
        except Exception:  # noqa
            pass
    d.text((M, y), "MUNDARIJA", font=_f(28, True), fill=GOLD)
    y += 48
    f_toc, f_tocb = _f(27), _f(27, True)
    pno = 2
    toc = []
    for c in cats:
        chunks = -(-len(c["items"]) // 20)
        toc.append((c["name"] if c["name"] != "Asosiy" else "Asosiy", len(c["items"]), pno))
        pno += chunks
    for name, n, pg in toc:
        if y > H - 150:
            break
        d.text((M + 10, y), name, font=f_tocb, fill=DARK)
        d.text((W - M - 10, y), f"{n} ta  ·  {pg}-sahifa", font=f_toc, fill=GREY, anchor="ra")
        y += 34
        d.line([(M, y + 6), (W - M, y + 6)], fill=GOLD_L, width=1)
        y += 18
    pages.append(im)

    gap = 24
    state_of = state_of or (lambda it: "")
    page_no = 1
    for c in cats:
        items = c["items"]
        for part in range(0, len(items), 20):
            chunk = items[part:part + 20]
            page_no += 1
            im, d, y = _new_page(title, "", page_no)
            name = c["name"] if c["name"] != "Asosiy" else "Asosiy"
            d.text((W // 2, y + 4), name.upper(), font=_f(44, True), fill=GOLD, anchor="mt")
            sub = f"{len(items)} ta mahsulot" + (f" · {part // 20 + 1}-qism" if len(items) > 20 else "")
            d.text((W // 2, y + 62), sub, font=_f(22), fill=GREY, anchor="mt")
            y += 110
            aw, ah = W - 2 * M, H - 110 - y
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
                _card(im, d, x0 + col * (cw + gap), y0 + r * (card_h + gap), cw, card_h, ph, it,
                      photos.get(it["id"]), state_of(it))
            pages.append(im)

    buf = io.BytesIO()
    pages[0].save(buf, "PDF", resolution=150, save_all=True, append_images=pages[1:],
                  title=f"LENON katalog — {title}", author="LENON")
    return buf.getvalue()
