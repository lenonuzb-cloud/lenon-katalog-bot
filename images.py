"""1-agent: rasmni tahrirlash (format, sifat).  2-agent: logo qo'yish.
Shuningdek, zaxira usuli uchun jadvalni rasm qilib chizish."""
import io
import time

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageEnhance

import config

try:  # iPhone/Samsung HEIC rasmlarini ham o'qiy olish uchun
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:  # noqa
    pass

SIZE = 1280
_logo_cache = None


def _logo() -> Image.Image:
    global _logo_cache
    if _logo_cache is None:
        _logo_cache = Image.open(config.ASSETS / "logo.png").convert("RGBA")
    return _logo_cache


def _font(size, bold=False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for path in (config.ASSETS / name, "/usr/share/fonts/truetype/dejavu/" + name):
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            pass
    return ImageFont.load_default(size)


# ---------------- 1-agent: rasmni tahrirlash ----------------
def fix_format(data: bytes) -> Image.Image:
    """Rasmni to'g'ri burchakka buradi, yorug'ligini tekislaydi va kvadrat (1:1) qiladi."""
    im = Image.open(io.BytesIO(data))
    im = ImageOps.exif_transpose(im)
    im = im.convert("RGB")

    # yengil sifat tuzatish: kontrast va tiniqlik
    im = ImageOps.autocontrast(im, cutoff=0.5)
    im = ImageEnhance.Sharpness(im).enhance(1.15)
    im = ImageEnhance.Color(im).enhance(1.05)

    w, h = im.size
    ratio = w / h
    if 0.9 <= ratio <= 1.1:
        # deyarli kvadrat — markazdan qirqamiz
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        im = im.crop((left, top, left + side, top + side))
        return im.resize((SIZE, SIZE), Image.LANCZOS)

    # aks holda: mahsulot to'liq ko'rinsin, chetlari o'sha rasmning xiralashgan foni bilan to'ldiriladi
    bg = ImageOps.fit(im, (SIZE, SIZE), Image.LANCZOS)
    bg = bg.filter(ImageFilter.GaussianBlur(40))
    bg = ImageEnhance.Brightness(bg).enhance(0.85)
    fg = im.copy()
    fg.thumbnail((SIZE, SIZE), Image.LANCZOS)
    bg.paste(fg, ((SIZE - fg.width) // 2, (SIZE - fg.height) // 2))
    return bg


# ---------------- 2-agent: logo qo'yish ----------------
def add_logo(im: Image.Image) -> Image.Image:
    """Logoni rasmning yuqori qismiga, o'rtaga qo'yadi (ostida yengil soya bilan)."""
    im = im.convert("RGBA")
    W, H = im.size
    logo = _logo()
    lw = int(W * 0.40)
    lh = int(logo.height * lw / logo.width)
    lg = logo.resize((lw, lh), Image.LANCZOS)
    x = (W - lw) // 2
    y = int(H * 0.035)

    # soya — logo har qanday fonda ko'rinishi uchun
    shadow = Image.new("RGBA", im.size, (0, 0, 0, 0))
    alpha = lg.split()[3].point(lambda a: int(a * 0.55))
    sh = Image.new("RGBA", lg.size, (0, 0, 0, 255))
    sh.putalpha(alpha)
    shadow.paste(sh, (x + 3, y + 4), sh)
    shadow = shadow.filter(ImageFilter.GaussianBlur(6))
    im = Image.alpha_composite(im, shadow)
    im.alpha_composite(lg, (x, y))
    return im.convert("RGB")


def to_jpeg(im: Image.Image, quality=90) -> bytes:
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


GOLD = (214, 163, 30)  # logo rangi


def add_label(im: Image.Image, lines: list) -> Image.Image:
    """Mahsulot ma'lumotini (o'lcham, nomi, kodi, narxi) rasmning chap-pastki burchagiga oltin rangda yozadi."""
    lines = [str(x).strip() for x in lines if x and str(x).strip()]
    if not lines:
        return im
    im = im.convert("RGBA")
    W, H = im.size
    size = int(W * 0.036)
    font = None
    for path in (config.ASSETS / "label.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"):
        try:
            font = ImageFont.truetype(str(path), size)
            break
        except OSError:
            pass
    if font is None:
        font = _font(size)
    step = int(size * 1.32)
    x = int(W * 0.02)
    y = H - int(H * 0.06) - step * len(lines)
    # yengil soya — har qanday fonda o'qilishi uchun
    shadow = Image.new("RGBA", im.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    for i, t in enumerate(lines):
        sd.text((x + 2, y + i * step + 2), t, font=font, fill=(0, 0, 0, 170))
    shadow = shadow.filter(ImageFilter.GaussianBlur(3))
    im = Image.alpha_composite(im, shadow)
    d = ImageDraw.Draw(im)
    for i, t in enumerate(lines):
        d.text((x, y + i * step), t, font=font, fill=GOLD + (255,))
    return im.convert("RGB")


def process(data: bytes, logo: bool = True, label: list | None = None) -> bytes:
    """1 + 2-agent birga: format -> logo -> (mahsulot yozuvi) -> JPEG."""
    im = fix_format(data)
    if logo:
        im = add_logo(im)
    if label:
        im = add_label(im, label)
    return to_jpeg(im)


def thumbnail(data: bytes, width: int) -> bytes:
    im = Image.open(io.BytesIO(data)).convert("RGB")
    im.thumbnail((width, width), Image.LANCZOS)
    return to_jpeg(im, 82)


# ---------------- jadvalni rasm qilib chizish (zaxira usuli) ----------------
def render_table(title: str, rows: list, note: str = "") -> bytes:
    """rows: [("cat", nom)] yoki [("item", no, nom, kod, narx, holat)] — holat: '', 'new', 'keldi', 'qolmadi'."""
    f = _font(26)
    fb = _font(26, True)
    ft = _font(38, True)
    pad = 28
    col = [70, 380, 170, 130]  # No, Nomi, Kodi, Narxi
    has_code = any(r[0] == "item" and r[3] for r in rows)
    if not has_code:
        col = [70, 500, 0, 150]
    W = pad * 2 + sum(col)
    rh = 46
    H = pad * 2 + 70 + rh * (len(rows) + 1) + (40 if note else 0)
    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    gold = (196, 150, 30)
    d.text((W // 2, pad + 22), title, font=ft, fill=gold, anchor="mm")
    y = pad + 60
    xs = [pad]
    for c in col:
        xs.append(xs[-1] + c)

    def cell(i, text, font, fill="black", anchor="lm"):
        if col[i] == 0:
            return
        if anchor == "mm":
            d.text(((xs[i] + xs[i + 1]) // 2, y + rh // 2), text, font=font, fill=fill, anchor="mm")
        else:
            d.text((xs[i] + 12, y + rh // 2), text, font=font, fill=fill, anchor="lm")

    d.rectangle([pad, y, W - pad, y + rh], fill=(240, 240, 240))
    for i, h in enumerate(["No.", "Nomi", "Kodi", "Narxi"]):
        cell(i, h, fb, anchor="mm" if i == 0 else "lm")
    y += rh
    for r in rows:
        d.line([pad, y, W - pad, y], fill=(215, 215, 215), width=1)
        if r[0] == "cat":
            d.rectangle([pad, y, W - pad, y + rh], fill=(246, 241, 226))
            d.text((W // 2, y + rh // 2), r[1], font=fb, fill=(90, 70, 20), anchor="mm")
        else:
            _, no, name, code, price, state = r
            color = "black"
            if state == "qolmadi":
                color = (200, 30, 30)
                name = name + "  - QOLMADI"
            elif state == "new":
                color = (200, 30, 30)
                name = name + "  NEW"
            elif state == "keldi":
                color = (20, 140, 60)
                name = name + "  - KELDI"
            cell(0, str(no), f, anchor="mm")
            cell(1, name, f, fill=color)
            cell(2, code, f)
            cell(3, price, f)
        y += rh
    d.rectangle([pad, pad + 60, W - pad, y], outline=(200, 200, 200), width=2)
    if note:
        d.text((W // 2, y + 22), note, font=fb, fill=(200, 30, 30), anchor="mm")
    return to_jpeg(im, 92)
