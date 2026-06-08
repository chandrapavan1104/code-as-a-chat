import asyncio
import logging
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

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("telegram_bot")

TG_MAX_LEN = 4000  # safe margin under Telegram's 4096 char limit
REQUEST_TIMEOUT = 600.0

# Telegram /command  →  orchestrator command name
COMMAND_MAP = {
    "shell": "shell",
    "claude": "claude",
    "codex": "codex",
    "gemini": "gemini",
    "status": "status",
    "files": "files",
    "sessions": "sessions",
    "projects": "projects",
    "memory": "memory",
    "ports": "ports",
    "firebase": "firebase",
    "usage": "usage",
    "notes": "notes",
    "context": "context",
    "remind": "reminders",
    "reminders": "reminders",
    "mac": "mac",
}


# ── auth ──────────────────────────────────────────────────────────────────────

def is_authorized(update: Update) -> bool:
    user = update.effective_user
    if user is None:
        return False
    return user.id in config.TELEGRAM_ALLOWED_USERS


# ── orchestrator HTTP call ────────────────────────────────────────────────────

async def call_orchestrator(command: str, prompt: str, session_id: str | None = None) -> str:
    url = f"{config.ORCHESTRATOR_URL.rstrip('/')}/run"
    payload: dict = {"command": command, "prompt": prompt}
    if session_id is not None:
        payload["session_id"] = session_id
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            r = await client.post(url, json=payload)
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

    text = (
        "Code-as-a-Chat is live.\n\n"
        "Commands:\n"
        "  /shell    <msg>     — smart router (LLM, mobile-formatted)\n"
        "  /claude   <prompt>  — Claude Code (raw, full output)\n"
        "  /codex    <prompt>  — OpenAI Codex (raw)\n"
        "  /gemini   <prompt>  — Google Gemini (raw)\n"
        "  /sessions [arg]     — browse past chats\n"
        "  /projects [arg]     — switch active workspace\n"
        "  /memory   [arg]     — show/count/clear shell memory\n"
        "  /forget             — wipe this conversation's memory\n"
        "  /ports    [arg]     — listening ports, kill processes\n"
        "  /firebase [arg]     — deploy/preview Firebase from this workspace\n"
        "  /usage    [arg]     — LLM provider usage (via codaur)\n"
        "  /notes    [arg]     — whiteboard: bugs/ideas/todos auto-tagged by project\n"
        "  /context  [arg]     — shared project context across all agents\n"
        "  /remind   <when><what> — time-based Telegram alert\n"
        "  /mac      <action>  — lock/say/notify/screenshot/photo\n"
        "  /lock               — lock the Mac screen\n"
        "  /status             — CPU/RAM/disk\n"
        "  /files    <path>    — list dir or read file\n"
        "  /help               — this menu\n\n"
        f"Free-text routes to /{config.DEFAULT_SKILL}."
    )
    await update.message.reply_text(text)


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


async def handle_freetext(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        await update.message.reply_text("Not authorized.")
        log.warning("Rejected user %s", update.effective_user)
        return
    prompt = (update.message.text or "").strip()
    if not prompt:
        return
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
    for tg_cmd, orch_cmd in COMMAND_MAP.items():
        app.add_handler(CommandHandler(tg_cmd, make_command_handler(orch_cmd)))
    # /forget is a convenience alias for "/memory clear"
    app.add_handler(CommandHandler("forget", make_command_handler("memory", "clear")))
    # /lock is a convenience alias for "/mac lock"
    app.add_handler(CommandHandler("lock", make_command_handler("mac", "lock")))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_freetext))

    log.info("Bot starting — allowlist: %s, orchestrator: %s",
             config.TELEGRAM_ALLOWED_USERS, config.ORCHESTRATOR_URL)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
