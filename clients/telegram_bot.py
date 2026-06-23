import asyncio
import logging
import time
from pathlib import Path

import httpx
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from server import config
from server.skills import discover, command_map, manifest

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("telegram_bot")

TG_MAX_LEN = 4000  # safe margin under Telegram's 4096 char limit
REQUEST_TIMEOUT = 600.0

# Incoming media (photos, files, stickers) gets saved here so the claude skill
# can Read it. Cleaned of week-old files on startup.
INBOX_DIR = Path.home() / ".codeasachat" / "inbox"
INBOX_MAX_AGE = 7 * 86400


def _cleanup_inbox() -> None:
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - INBOX_MAX_AGE
    for f in INBOX_DIR.iterdir():
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            continue

# Telegram /command → skill name, built from skill manifests at startup.
# Special prepend-aliases (inject a sub-argument) stay separate.
discover()
COMMAND_MAP: dict[str, str] = command_map()
PREPEND_ALIASES = {
    "forget": ("memory", "clear"),  # /forget == /memory clear
    "lock": ("mac", "lock"),        # /lock   == /mac lock
}


# ── auth ──────────────────────────────────────────────────────────────────────

def is_authorized(update: Update) -> bool:
    user = update.effective_user
    if user is None:
        return False
    return user.id in config.TELEGRAM_ALLOWED_USERS


# ── orchestrator HTTP call ────────────────────────────────────────────────────

def _auth_headers() -> dict:
    return {"X-API-Token": config.API_TOKEN} if config.API_TOKEN else {}


async def call_orchestrator(command: str, prompt: str, session_id: str | None = None) -> str:
    url = f"{config.ORCHESTRATOR_URL.rstrip('/')}/run"
    payload: dict = {"command": command, "prompt": prompt}
    if session_id is not None:
        payload["session_id"] = session_id
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            r = await client.post(url, json=payload, headers=_auth_headers())
            r.raise_for_status()
            return r.json().get("result", "(no result)")
    except httpx.HTTPStatusError as exc:
        return f"[orchestrator HTTP {exc.response.status_code}] {exc.response.text[:500]}"
    except httpx.HTTPError as exc:
        return f"[orchestrator error] {exc}"


# ── reply helpers ─────────────────────────────────────────────────────────────

def chunk(text: str, n: int = TG_MAX_LEN) -> list[str]:
    if len(text) <= n:
        return [text] if text else ["(empty response)"]
    return [text[i:i + n] for i in range(0, len(text), n)]


async def keep_typing(bot, chat_id: int, stop: asyncio.Event) -> None:
    """Refresh the typing indicator every ~4s until told to stop."""
    while not stop.is_set():
        try:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=4.0)
        except asyncio.TimeoutError:
            continue


def _session_id_for(update: Update) -> str:
    """Stable per-chat memory key, namespaced so other clients can't collide."""
    return f"tg:{update.effective_chat.id}"


async def run_with_typing(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                          command: str, prompt: str) -> None:
    stop = asyncio.Event()
    typing_task = asyncio.create_task(
        keep_typing(ctx.bot, update.effective_chat.id, stop)
    )
    try:
        result = await call_orchestrator(
            command, prompt, session_id=_session_id_for(update)
        )
    finally:
        stop.set()
        await typing_task

    for piece in chunk(result):
        await update.message.reply_text(piece)


# ── handlers ──────────────────────────────────────────────────────────────────

async def handle_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        await update.message.reply_text("Not authorized.")
        return

    lines = ["Gajala is live mava 🔥 (from Washington DC)", "", "Commands:"]
    for sk in manifest():
        lines.append(f"  /{sk['command']:<9} — {sk['help_line']}")
    lines += [
        "  /forget    — wipe this conversation's memory",
        "  /lock      — lock the Mac screen",
        "  /help      — this menu",
        "",
        f"Free-text routes to /{config.DEFAULT_SKILL}.",
    ]
    await update.message.reply_text("\n".join(lines))


def make_command_handler(orch_command: str, prepend_prompt: str = ""):
    """
    Returns a handler that maps a Telegram /command to an orchestrator command.
    `prepend_prompt` prefixes user args — used to make /forget == /memory clear.
    """
    async def handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not is_authorized(update):
            await update.message.reply_text("Not authorized.")
            log.warning("Rejected user %s", update.effective_user)
            return
        user_args = " ".join(ctx.args) if ctx.args else ""
        prompt = (prepend_prompt + " " + user_args).strip() if prepend_prompt else user_args
        await run_with_typing(update, ctx, orch_command, prompt)
    return handler


def _reply_context(update: Update) -> str:
    """If the user replied to an earlier message, quote it so the shell has
    the context of what's being replied to."""
    replied = update.message.reply_to_message if update.message else None
    if replied is None:
        return ""
    quoted = (replied.text or replied.caption or "").strip()
    if not quoted:
        kind = "a photo" if replied.photo else \
               "a sticker" if replied.sticker else \
               "a file" if replied.document else "an earlier message"
        return f"[Replying to {kind}]\n"
    if len(quoted) > 400:
        quoted = quoted[:397] + "…"
    return f"[Replying to earlier message: \"{quoted}\"]\n"


async def _download_media(tg_media, suffix: str) -> Path:
    """Download a Telegram media object into the inbox; return the local path."""
    file = await tg_media.get_file()
    path = INBOX_DIR / f"tg_{int(time.time())}_{file.file_unique_id}{suffix}"
    await file.download_to_drive(custom_path=str(path))
    return path


async def handle_freetext(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        await update.message.reply_text("Not authorized.")
        log.warning("Rejected user %s", update.effective_user)
        return
    prompt = (update.message.text or "").strip()
    if not prompt:
        return
    await run_with_typing(update, ctx, config.DEFAULT_SKILL,
                          _reply_context(update) + prompt)


async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    try:
        path = await _download_media(update.message.photo[-1], ".jpg")
    except Exception as exc:
        await update.message.reply_text(f"Couldn't download that image: {exc}")
        return
    caption = (update.message.caption or "").strip()
    prompt = (_reply_context(update)
              + f"[User sent an image, saved at: {path}]\n"
              + (caption or "(no caption — describe what you see and ask what they need)"))
    await run_with_typing(update, ctx, config.DEFAULT_SKILL, prompt)


async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    doc = update.message.document
    name = doc.file_name or "file"
    suffix = Path(name).suffix or ""
    try:
        path = await _download_media(doc, suffix)
    except Exception as exc:
        await update.message.reply_text(f"Couldn't download that file: {exc}")
        return
    caption = (update.message.caption or "").strip()
    prompt = (_reply_context(update)
              + f"[User sent a file '{name}' ({doc.mime_type or 'unknown type'}), "
              + f"saved at: {path}]\n"
              + (caption or "(no caption — inspect the file and summarize it)"))
    await run_with_typing(update, ctx, config.DEFAULT_SKILL, prompt)


async def handle_sticker(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    st = update.message.sticker
    emoji = st.emoji or "?"
    set_name = st.set_name or "unknown set"
    if st.is_animated or st.is_video:
        prompt = (f"[User sent an animated sticker — emoji {emoji}, set '{set_name}'. "
                  f"You can't view the animation; react in persona based on the emoji.]")
    else:
        try:
            path = await _download_media(st, ".webp")
            prompt = (f"[User sent a sticker — emoji {emoji}, set '{set_name}', "
                      f"image saved at: {path}. React in persona; only inspect the "
                      f"image if the emoji isn't enough.]")
        except Exception:
            prompt = (f"[User sent a sticker — emoji {emoji}, set '{set_name}'. "
                      f"React in persona based on the emoji.]")
    await run_with_typing(update, ctx, config.DEFAULT_SKILL,
                          _reply_context(update) + prompt)


async def handle_voice_video(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    msg = update.message
    if msg.voice:
        desc = f"a voice note ({msg.voice.duration}s)"
    elif msg.audio:
        desc = f"an audio file ({msg.audio.duration}s)"
    elif msg.video_note:
        desc = f"a video note ({msg.video_note.duration}s)"
    else:
        desc = "a video"
    prompt = (f"[User sent {desc}. You cannot transcribe or watch audio/video yet — "
              f"tell them honestly, in persona, and ask them to type it.]")
    await run_with_typing(update, ctx, config.DEFAULT_SKILL,
                          _reply_context(update) + prompt)


async def handle_other(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Catch-all: location, contacts, polls, dice... anything else gets a reply."""
    if not is_authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    msg = update.message
    if msg is None:
        return
    kind = ("a location" if msg.location else
            "a contact" if msg.contact else
            "a poll" if msg.poll else
            "a dice/emoji game" if msg.dice else
            "an unsupported message type")
    prompt = (f"[User sent {kind}. You can't process this content type — "
              f"acknowledge it in persona and ask what they need.]")
    await run_with_typing(update, ctx, config.DEFAULT_SKILL, prompt)


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if not config.TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN not set in .env")
    if not config.TELEGRAM_ALLOWED_USERS:
        raise SystemExit(
            "TELEGRAM_ALLOWED_USERS not set in .env "
            "(refusing to run without an allowlist)"
        )

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("help", handle_start))
    # Auto-registered from skill manifests (no hardcoded command list).
    for tg_cmd, skill_name in COMMAND_MAP.items():
        app.add_handler(CommandHandler(tg_cmd, make_command_handler(skill_name)))
    # Special prepend-aliases that inject a sub-argument.
    for alias, (skill_name, prepend) in PREPEND_ALIASES.items():
        app.add_handler(CommandHandler(alias, make_command_handler(skill_name, prepend)))

    # Media + text + catch-all (first matching handler wins, so order matters)
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.Sticker.ALL, handle_sticker))
    app.add_handler(MessageHandler(
        filters.VOICE | filters.AUDIO | filters.VIDEO | filters.VIDEO_NOTE,
        handle_voice_video))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_freetext))
    app.add_handler(MessageHandler(~filters.COMMAND, handle_other))

    _cleanup_inbox()
    log.info("Bot starting — allowlist: %s, orchestrator: %s",
             config.TELEGRAM_ALLOWED_USERS, config.ORCHESTRATOR_URL)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
