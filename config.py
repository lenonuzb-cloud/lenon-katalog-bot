"""Sozlamalar — hammasi server muhit o'zgaruvchilaridan (Environment) olinadi."""
import os
from datetime import timezone, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ASSETS = BASE_DIR
WEBAPP_DIR = BASE_DIR
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
CACHE_DIR = Path(os.getenv("CACHE_DIR", "/tmp/lenon_cache"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# BotFather bergan maxfiy kalit
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Bot egasining Telegram username'i (@ belgisisiz). Shu odam botga /start yozganda egasi bo'ladi.
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "").strip().lstrip("@").lower()

# Serverning ochiq manzili (Render buni o'zi beradi: RENDER_EXTERNAL_URL)
PUBLIC_URL = (os.getenv("PUBLIC_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")
PORT = int(os.getenv("PORT", "8080"))

# Toshkent vaqti (UTC+5, yozgi vaqt yo'q)
TZ = timezone(timedelta(hours=5))

# Belgilar necha kun turadi
NEW_DAYS = 7
KELDI_DAYS = 7

# Kunlik hisobot soati (Toshkent vaqti)
REPORT_HOUR = int(os.getenv("REPORT_HOUR", "21"))

# Guruhga o'zgarishlar haqida xabar necha soniya kutib, birga yuboriladi
NOTIFY_DELAY = int(os.getenv("NOTIFY_DELAY", "90"))

# Boy (rich) xabarlardan foydalanish: auto | off
RICH_MODE = os.getenv("RICH_MODE", "auto")

# Telegram'dagi agentlar (tugmalar)
DEFAULT_AGENTS = {
    "Ibrohim": "ibroxim_4346",
    "Azamat": "azamat_7771",
    "Abduvosit": "abduvosit104",
}

# Guruh bo'limlari (bo'lim kaliti -> nomi)
TOPIC_KEYS = {
    "lenon": "LENON",
    "boden": "BODEN",
    "rasmlar": "Rasmlar",
    "nabor44": "LENON nabor 44",
    "nabor33": "LENON nabor 33",
    "nabor39": "LENON nabor 39",
    "muhokama": "Muhokama",
}
