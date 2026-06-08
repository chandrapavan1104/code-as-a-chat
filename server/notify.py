"""
Outbound push to Telegram — lets the SERVER message you proactively
(reminders, low-battery alerts, screenshots, webcam snaps), independent of
the request/response bot flow.

Uses the same bot token; defaults to the first allowlisted user as the target
chat (for a private chat, chat_id == user_id).
"""

import httpx
from server import config

_API = "https://api.telegram.org"


def default_chat_id() -> int | None:
    return config.TELEGRAM_ALLOWED_USERS[0] if config.TELEGRAM_ALLOWED_USERS else None


async def push_text(text: str, chat_id: int | None = None) -> bool:
    token = config.TELEGRAM_BOT_TOKEN
    cid = chat_id or default_chat_id()
    if not token or not cid:
        return False
    url = f"{_API}/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(url, json={"chat_id": cid, "text": text})
            return r.status_code == 200
    except httpx.HTTPError:
        return False


async def push_photo(path: str, caption: str = "", chat_id: int | None = None) -> bool:
    token = config.TELEGRAM_BOT_TOKEN
    cid = chat_id or default_chat_id()
    if not token or not cid:
        return False
    url = f"{_API}/bot{token}/sendPhoto"
    try:
        with open(path, "rb") as f:
            photo_bytes = f.read()
    except OSError:
        return False
    files = {"photo": ("capture.jpg", photo_bytes, "image/jpeg")}
    data = {"chat_id": str(cid)}
    if caption:
        data["caption"] = caption
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(url, data=data, files=files)
            return r.status_code == 200
    except httpx.HTTPError:
        return False
