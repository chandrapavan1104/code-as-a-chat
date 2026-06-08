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

# ── Background scheduler (reminders + battery alerts) ─────────────────────────
SCHEDULER_INTERVAL: int = int(os.getenv("SCHEDULER_INTERVAL", "60"))   # seconds
BATTERY_THRESHOLD: int = int(os.getenv("BATTERY_THRESHOLD", "20"))     # percent
BATTERY_ALERT_COOLDOWN: int = int(os.getenv("BATTERY_ALERT_COOLDOWN", "1800"))  # seconds


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
