"""
Small, phone-settable preferences that persist in ~/.codeasachat/state.json
(alongside the active workspace).

Right now that's just the **pinned coding engine** — which model the shell agent
should prefer for code/file work. Default "auto" keeps today's behavior (Haiku
picks per ask). Pinning makes the active (directory, model) session unambiguous,
which is what the chat header shows and what the session-reuse store keys on.
"""

import json
from pathlib import Path

STATE_FILE = Path.home() / ".codeasachat" / "state.json"

# User-facing engine names. "auto" = let the agent choose (today's behavior).
CODING_ENGINES = ("auto", "claude", "codex", "gemini")

# Engine name → the shell tool that runs it. Gemini's skill is registered under
# "antigravity" for historical reasons, so map it here.
ENGINE_TOOL = {"claude": "claude", "codex": "codex", "gemini": "antigravity"}


def _load() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def get_coding_engine() -> str:
    val = (_load().get("coding_engine") or "auto").lower()
    return val if val in CODING_ENGINES else "auto"


def set_coding_engine(engine: str) -> str:
    engine = (engine or "auto").lower()
    if engine not in CODING_ENGINES:
        raise ValueError(f"unknown engine {engine!r}; pick one of {CODING_ENGINES}")
    state = _load()
    state["coding_engine"] = engine
    _save(state)
    return engine
