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

# Engines that carry a selectable model (everything except "auto").
MODEL_ENGINES = ("claude", "codex", "gemini")

# Static model presets. Claude uses stable aliases; Gemini a small set. Codex is
# read live from its own model cache (see model_presets) so the list is current.
CLAUDE_MODELS = ("opus", "sonnet", "haiku")
GEMINI_MODELS = ("gemini-2.5-pro", "gemini-2.5-flash")
_CODEX_FALLBACK = ("gpt-5.6-sol", "gpt-5.5", "gpt-5.4-mini")


def _codex_models() -> list[str]:
    """Read the codex CLI's cached model list so presets match what's installed.
    Falls back to a small static list if the cache isn't readable."""
    try:
        data = json.loads((Path.home() / ".codex" / "models_cache.json").read_text())
        slugs = [m["slug"] for m in data.get("models", [])
                 if m.get("visibility") == "list" and m.get("slug")]
        return slugs or list(_CODEX_FALLBACK)
    except Exception:
        return list(_CODEX_FALLBACK)


def model_presets() -> dict:
    """Per-engine selectable models for the UI + chat resolver."""
    return {
        "claude": list(CLAUDE_MODELS),
        "codex": _codex_models(),
        "gemini": list(GEMINI_MODELS),
    }


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


def get_coding_models() -> dict:
    """Per-engine pinned model. Empty string = let the CLI use its default."""
    m = _load().get("coding_models") or {}
    return {e: (m.get(e) or "") for e in MODEL_ENGINES}


def get_coding_model(engine: str) -> str:
    """The pinned model for one engine ('' if none — the CLI default)."""
    return get_coding_models().get((engine or "").lower(), "")


def set_coding_model(engine: str, model: str) -> str:
    engine = (engine or "").lower()
    if engine not in MODEL_ENGINES:
        raise ValueError(f"unknown engine {engine!r}; pick one of {MODEL_ENGINES}")
    state = _load()
    models = state.get("coding_models") or {}
    models[engine] = (model or "").strip()
    state["coding_models"] = models
    _save(state)
    return models[engine]
