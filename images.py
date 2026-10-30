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
                name = ("× " + name) if config.LENONUZ else (name + "  - QOLMADI")
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


# ---------------- mavjud rasmdagi yozuvni tekshirish / tuzatish ----------------
def _label_geom(W, H, n):
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
    y = H - int(H * 0.06) - step * n
    return font, step, x, y


def label_match(im: Image.Image, lines: list) -> float:
    """Rasmda aynan shu yozuv (shu joyda) bormi? 0..1 — oltin rangli harf piksellari ulushi."""
    lines = [str(x).strip() for x in lines if x and str(x).strip()]
    if not lines:
        return 0.0
    im = im.convert("RGB")
    W, H = im.size
    font, step, x, y = _label_geom(W, H, len(lines))
    mask = Image.new("L", im.size, 0)
    md = ImageDraw.Draw(mask)
    for i, t in enumerate(lines):
        md.text((x, y + i * step), t, font=font, fill=255)
    # harflarning faqat ichki qismi (chetlari JPEG da xiralashadi)
    mask = mask.filter(ImageFilter.MinFilter(3))
    bbox = mask.getbbox()
    if not bbox:
        return 0.0
    crop_m = mask.crop(bbox)
    crop_i = im.crop(bbox)
    total = hit = 0
    for mv, (r, g, b) in zip(crop_m.getdata(), crop_i.getdata()):
        if mv > 200:
            total += 1
            if abs(r - GOLD[0]) < 45 and abs(g - GOLD[1]) < 45 and abs(b - GOLD[2]) < 55:
                hit += 1
    return hit / total if total else 0.0


def erase_label(im: Image.Image, lines: list) -> Image.Image:
    """Eski yozuvni o'chiradi: har bir ustunni yozuvdan yuqoridagi va pastdagi fon orasida
    tekis o'tish bilan to'ldiradi (fon gradienti saqlanadi)."""
    lines = [str(x).strip() for x in lines if x and str(x).strip()]
    im = im.convert("RGB")
    W, H = im.size
    font, step, x, y = _label_geom(W, H, len(lines))
    d = ImageDraw.Draw(im)
    wmax = max(d.textlength(t, font=font) for t in lines)
    pad = 16
    x0, y0 = max(0, x - pad), max(1, y - pad)
    x1, y1 = min(W, int(x + wmax + pad + 6)), min(H - 1, y + step * len(lines) + pad)
    px = im.load()
    patch = Image.new("RGB", (x1 - x0, y1 - y0))
    pp = patch.load()
    hgt = y1 - y0
    for cx in range(x0, x1):
        a_ = [px[cx, max(0, y0 - k)] for k in range(1, 6)]
        b_ = [px[cx, min(H - 1, y1 + k)] for k in range(1, 6)]
        ta = tuple(sum(v[i] for v in a_) / 5 for i in range(3))
        tb = tuple(sum(v[i] for v in b_) / 5 for i in range(3))
        for yy in range(hgt):
            t = yy / max(1, hgt - 1)
            pp[cx - x0, yy] = tuple(int(ta[i] * (1 - t) + tb[i] * t) for i in range(3))
    patch = patch.filter(ImageFilter.GaussianBlur(4))
    m = Image.new("L", patch.size, 255)
    edge = Image.new("L", patch.size, 0)
    ImageDraw.Draw(edge).rectangle([4, 4, patch.width - 5, patch.height - 5], fill=255)
    m = edge.filter(ImageFilter.GaussianBlur(3))
    im.paste(patch, (x0, y0), m)
    return im


def relabel_image(data: bytes, old_lines: list, new_lines: list) -> tuple[bytes | None, str]:
    """Tayyor rasmdagi yozuvni yangilaydi.
    Qaytaradi: (yangi jpeg yoki None, holat): 'ok' — o'zgartirish kerak emas, 'added' — yozuv qo'shildi,
    'fixed' — eski yozuv almashtirildi."""
    im = Image.open(io.BytesIO(data)).convert("RGB")
    if label_match(im, new_lines) > 0.6:
        return None, "ok"
    if label_match(im, old_lines) > 0.6:
        im = erase_label(im, old_lines)
        return to_jpeg(add_label(im, new_lines)), "fixed"
    return to_jpeg(add_label(im, new_lines)), "added"
