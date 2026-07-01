import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")

TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ALLOWED_USERS: list[int] = [
    int(uid.strip())
    for uid in os.getenv("TELEGRAM_ALLOWED_USERS", "").split(",")
    if uid.strip().isdigit()
]

DEFAULT_SKILL: str = os.getenv("DEFAULT_SKILL", "claude")

_workspace_env = os.getenv("WORKSPACE_DIR", "")
WORKSPACE_DIR: Path = Path(_workspace_env).expanduser() if _workspace_env else Path.home()

ORCHESTRATOR_URL: str = os.getenv("ORCHESTRATOR_URL", "http://localhost:8000")


def _load_or_create_api_token() -> str:
    """Shared secret guarding the /run and /skills endpoints.
    Priority: API_TOKEN env → persisted token file → freshly generated.
    Server and bot both read this so they agree on the token automatically."""
    env = os.getenv("API_TOKEN", "").strip()
    if env:
        return env
    token_file = Path.home() / ".codeasachat" / "api_token"
    if token_file.exists():
        existing = token_file.read_text().strip()
        if existing:
            return existing
    import secrets
    token = secrets.token_urlsafe(32)
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(token)
    try:
        token_file.chmod(0o600)
    except OSError:
        pass
    return token


API_TOKEN: str = _load_or_create_api_token()

# LLM that powers the shell skill (routing + mobile reformatting).
# 'haiku' is fast and cheap; override to 'sonnet' / 'opus' / full model name if you want.
SHELL_MODEL: str = os.getenv("SHELL_MODEL", "haiku")

# How many recent turn-pairs the shell pulls from memory into Haiku's context.
# Higher = better continuity, more tokens per call.
# Bumped from 5 to 12 after losing project-priority context across long chats.
MEMORY_TURNS: int = int(os.getenv("MEMORY_TURNS", "12"))

# Daily token budgets used by the /usage skill to compute a "% used today"
# for providers that don't expose a real rate-limit snapshot locally.
# Defaults are rough heuristics — tune to your actual subscription tier.
#   Claude Pro: ~5M billable tokens/day is a heavy day
#   Gemini free: extremely generous; 10M is a reasonable headroom marker
USAGE_BUDGET_CLAUDE: int = int(os.getenv("USAGE_BUDGET_CLAUDE", "5000000"))
USAGE_BUDGET_GEMINI: int = int(os.getenv("USAGE_BUDGET_GEMINI", "10000000"))

# After each claude/codex/gemini run, converge the shared context files
# (AGENTS.md / CLAUDE.md / GEMINI.md) so whichever agent updated its file
# propagates to the others. Disable with CONTEXT_AUTO_SYNC=false.
CONTEXT_AUTO_SYNC: bool = os.getenv("CONTEXT_AUTO_SYNC", "true").lower() in ("1", "true", "yes")

# When switching into a project that has no context files yet, auto-create the
# template (AGENTS.md + mirrors) so every workspace is context-ready.
# Disable with CONTEXT_AUTO_INIT=false.
CONTEXT_AUTO_INIT: bool = os.getenv("CONTEXT_AUTO_INIT", "true").lower() in ("1", "true", "yes")

# ── Agent persona ─────────────────────────────────────────────────────────────
# Name + personality of the shell agent. Persona affects only the user-facing
# reply text — tool routing stays precise English under the hood.
AGENT_NAME: str = os.getenv("AGENT_NAME", "Gajala")
# Set AGENT_PERSONA=professional to switch back to a plain assistant voice.
AGENT_PERSONA: str = os.getenv("AGENT_PERSONA", "telugu-bestie")

# Model for the diary/Anna mentor skill. Mentor judgment is worth a better
# model than the fast shell router; entries are low-frequency.
DIARY_MODEL: str = os.getenv("DIARY_MODEL", "sonnet")

# ── Background scheduler (reminders + battery alerts) ─────────────────────────
SCHEDULER_INTERVAL: int = int(os.getenv("SCHEDULER_INTERVAL", "60"))   # seconds
# Battery alerts only make sense on a laptop; disable on an always-plugged
# desktop (e.g. Mac Mini) with BATTERY_ALERTS=false. Machines with no battery
# are skipped automatically regardless.
BATTERY_ALERTS: bool = os.getenv("BATTERY_ALERTS", "true").lower() in ("1", "true", "yes")
BATTERY_THRESHOLD: int = int(os.getenv("BATTERY_THRESHOLD", "20"))     # percent
BATTERY_ALERT_COOLDOWN: int = int(os.getenv("BATTERY_ALERT_COOLDOWN", "1800"))  # seconds

# ── FCM push (Gajala Android app) ─────────────────────────────────────────────
# Service-account key downloaded from Firebase console; lets the server send
# pushes to registered devices via the FCM HTTP v1 API. If the file is absent,
# push is silently skipped (Telegram still works).
FCM_SERVICE_ACCOUNT: Path = Path(
    os.getenv("FCM_SERVICE_ACCOUNT", str(Path.home() / ".codeasachat" / "fcm-service-account.json"))
).expanduser()


# Apply any persisted workspace override (from /projects switch …).
# Done inside a function so the import order stays clean.
def _apply_persisted_state() -> None:
    import json as _json
    state_file = Path.home() / ".codeasachat" / "state.json"
    if not state_file.exists():
        return
    try:
        state = _json.loads(state_file.read_text())
    except (OSError, _json.JSONDecodeError):
        return
    saved = state.get("workspace_dir")
    if not saved:
        return
    p = Path(saved).expanduser()
    if p.exists() and p.is_dir():
        global WORKSPACE_DIR
        WORKSPACE_DIR = p


_apply_persisted_state()
