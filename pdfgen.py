"""Dizayn katalogini chiroyli PDF qilib tayyorlash (oq + oltin uslubda, A4)."""
import io
import time
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont, ImageOps

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


def build(design: dict, section_title: str, photos: dict, cover: bytes | None = None,
          state_of=None) -> bytes:
    """design — katalogdagi dizayn; photos — {item_id: jpeg bytes}; state_of(item) -> 'qolmadi'|'keldi'|'new'|''"""
    title = design["title"]
    stamp = datetime.now(config.TZ).strftime("%d.%m.%Y")
    total = sum(len(c["items"]) for c in design["categories"])
    subtitle = f"{section_title} · {total} ta mahsulot · {stamp}"
    pages = []
    page_no = 1
    im, d, y = _new_page(title, subtitle, page_no)

    # muqova rasmi (dizaynning umumiy rasmi)
    if cover:
        try:
            c = Image.open(io.BytesIO(cover)).convert("RGB")
            cw = W - 2 * M
            ch = min(int(cw * c.height / c.width), 700)
            c = ImageOps.fit(c, (cw, ch), Image.LANCZOS)
            mask = Image.new("L", c.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, cw, ch], radius=28, fill=255)
            im.paste(c, (M, y), mask)
            y += ch + 40
        except Exception:  # noqa
            pass

    cols, gap = 4, 22
    cw = (W - 2 * M - gap * (cols - 1)) // cols
    ph_h = cw
    card_h = ph_h + 118
    f_name, f_code, f_price, f_badge, f_cat = _f(22, True), _f(18), _f(23, True), _f(16, True), _f(27, True)

    def need(hh):
        nonlocal im, d, y, page_no
        if y + hh > H - 110:
            pages.append(im)
            page_no += 1
            im, d, y = _new_page(title, "", page_no)

    for cat in design["categories"]:
        if not cat["items"]:
            continue
        need(60 + card_h)
        if cat["name"] and cat["name"] != "Asosiy":
            d.text((M, y), cat["name"].upper(), font=f_cat, fill=GOLD)
            y += 40
            d.line([(M, y), (W - M, y)], fill=GOLD_L, width=2)
            y += 18
        for i in range(0, len(cat["items"]), cols):
            need(card_h + gap)
            row = cat["items"][i:i + cols]
            for j, it in enumerate(row):
                x = M + j * (cw + gap)
                d.rounded_rectangle([x, y, x + cw, y + card_h], radius=22, fill=BG_CARD, outline=GOLD_L, width=2)
                raw = photos.get(it["id"])
                if raw:
                    try:
                        p = ImageOps.fit(Image.open(io.BytesIO(raw)).convert("RGB"), (cw - 16, ph_h - 16), Image.LANCZOS)
                        mask = Image.new("L", p.size, 0)
                        ImageDraw.Draw(mask).rounded_rectangle([0, 0, p.width, p.height], radius=16, fill=255)
                        im.paste(p, (x + 8, y + 8), mask)
                    except Exception:  # noqa
                        raw = None
                if not raw:
                    d.rounded_rectangle([x + 8, y + 8, x + cw - 8, y + ph_h - 8], radius=16, fill=(240, 235, 222))
                    lg = _logo(int(cw * 0.5))
                    lg.putalpha(lg.split()[3].point(lambda a: int(a * 0.35)))
                    im.paste(lg, (x + (cw - lg.width) // 2, y + (ph_h - lg.height) // 2), lg)
                st = state_of(it) if state_of else ""
                if st == "qolmadi":
                    # rasm ustiga xira parda
                    veil = Image.new("RGBA", (cw - 16, ph_h - 16), (255, 255, 255, 110))
                    im.paste(veil, (x + 8, y + 8), veil)
                    bw = d.textlength("QOLMADI", font=f_badge) + 24
                    _badge(d, x + cw - 14 - bw, y + 14, "QOLMADI", RED, f_badge)
                elif st == "keldi":
                    bw = d.textlength("KELDI", font=f_badge) + 24
                    _badge(d, x + cw - 14 - bw, y + 14, "KELDI", GREEN, f_badge)
                if st == "new" or it.get("is_new"):
                    _badge(d, x + 14, y + 14, "NEW", RED, f_badge)
                ty = y + ph_h + 6
                d.text((x + 14, ty), _fit_text(d, it["name"], f_name, cw - 28), font=f_name, fill=DARK)
                meta = " · ".join(v for v in (it.get("code", ""), it.get("size", "")) if v)
                d.text((x + 14, ty + 34), _fit_text(d, meta, f_code, cw - 28), font=f_code, fill=GREY)
                d.text((x + 14, ty + 66), it.get("price", ""), font=f_price,
                       fill=RED if st == "qolmadi" else GOLD)
            y += card_h + gap
        y += 10
    pages.append(im)
    buf = io.BytesIO()
    pages[0].save(buf, "PDF", resolution=150, save_all=True, append_images=pages[1:],
                  title=f"LENON katalog — {title}", author="LENON")
    return buf.getvalue()
