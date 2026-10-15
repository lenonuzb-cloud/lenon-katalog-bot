"""Telegram Bot API bilan ishlash uchun yengil mijoz (to'g'ridan-to'g'ri HTTP)."""
import asyncio
import json
import logging

import aiohttp

import config

log = logging.getLogger("tg")


class TelegramError(Exception):
    def __init__(self, method, code, description, params=None):
        super().__init__(f"{method}: {code} {description}")
        self.method = method
        self.code = code
        self.description = description or ""
        self.params = params or {}


class Bot:
    def __init__(self, token: str):
        self.token = token
        self.base = f"https://api.telegram.org/bot{token}/"
        self.file_base = f"https://api.telegram.org/file/bot{token}/"
        self.session: aiohttp.ClientSession | None = None
        self.me: dict = {}

    async def start(self):
        if self.session is None:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90))
        self.me = await self.call("getMe")
        return self.me

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None

    @property
    def username(self) -> str:
        return self.me.get("username", "")

    async def call(self, method: str, files: dict | None = None, _retries: int = 3, **params):
        """Bot API metodini chaqiradi. 429 (juda tez) bo'lsa kutib qayta urinadi."""
        params = {k: v for k, v in params.items() if v is not None}
        # -1 = guruhning umumiy (General) bo'limi: Telegram'ga raqamsiz yuboriladi
        if str(params.get("message_thread_id", "")) == "-1":
            params.pop("message_thread_id")
        for attempt in range(_retries + 1):
            try:
                if files:
                    form = aiohttp.FormData()
                    for k, v in params.items():
                        form.add_field(k, v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
                    for name, (filename, data, ctype) in files.items():
                        form.add_field(name, data, filename=filename, content_type=ctype)
                    async with self.session.post(self.base + method, data=form) as r:
                        res = await r.json(content_type=None)
                else:
                    async with self.session.post(self.base + method, json=params) as r:
                        res = await r.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt < _retries:
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                raise TelegramError(method, -1, f"tarmoq xatosi: {e}")
            if res.get("ok"):
                return res.get("result")
            code = res.get("error_code")
            desc = res.get("description", "")
            if code == 429 and attempt < _retries:
                wait = (res.get("parameters") or {}).get("retry_after", 5)
                await asyncio.sleep(min(wait, 60) + 1)
                continue
            raise TelegramError(method, code, desc, res.get("parameters"))
        raise TelegramError(method, -1, "noma'lum xato")

    async def download(self, file_id: str) -> bytes:
        f = await self.call("getFile", file_id=file_id)
        async with self.session.get(self.file_base + f["file_path"]) as r:
            r.raise_for_status()
            return await r.read()

    # --- qulay qisqartmalar ---
    async def send(self, chat_id, text, **kw):
        return await self.call("sendMessage", chat_id=chat_id, text=text,
                               parse_mode=kw.pop("parse_mode", "HTML"),
                               link_preview_options={"is_disabled": True}, **kw)

    async def send_photo_bytes(self, chat_id, data: bytes, caption=None, **kw):
        return await self.call("sendPhoto", files={"photo": ("photo.jpg", data, "image/jpeg")},
                               chat_id=str(chat_id), caption=caption,
                               parse_mode="HTML" if caption else None, **kw)

    async def answer_cb(self, cb_id, text=None, alert=False):
        try:
            await self.call("answerCallbackQuery", callback_query_id=cb_id, text=text, show_alert=alert)
        except TelegramError:
            pass

    async def safe_delete(self, chat_id, message_id):
        try:
            await self.call("deleteMessage", chat_id=chat_id, message_id=message_id)
            return True
        except TelegramError:
            return False


bot = Bot(config.BOT_TOKEN)
