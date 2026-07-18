"""
model skill — switch the coding engine and/or model from chat.

Fixes the old "switch to opus" behaviour where the agent had no way to actually
change anything and just claimed success. Now the shell agent calls this skill,
which persists the choice in prefs (state.json); the CLI wrappers pass the pinned
model via --model on the next coding run.

Accepts, via the prompt:
  auto | claude | codex | gemini          → switch engine (keep that engine's model)
  opus | sonnet | haiku                    → engine=claude, that model
  <a codex slug> (sol / terra / gpt-5.5 …) → engine=codex, that model
  gemini-2.5-pro | flash | pro             → engine=gemini, that model
  "<engine> <model>"                       → set both (e.g. "codex gpt-5.6-terra")
"""

from server import prefs
from server.skills.base import Skill
from server.skills import register

_CLAUDE_ALIASES = {"opus", "sonnet", "haiku", "fable"}


def _match_codex(text: str) -> str | None:
    """Fuzzy-match a token to a codex model slug (exact, substring, or the
    trailing keyword like 'sol' / 'terra' / 'mini'). 'tera' → 'terra'."""
    text = text.strip().lower().replace(" ", "-").replace("gpt5", "gpt-5")
    slugs = prefs._codex_models()
    for s in slugs:
        if s == text:
            return s
    if text in ("tera", "terra"):
        text = "terra"
    for s in slugs:
        if text and (text in s or s.split("-")[-1] == text):
            return s
    return None


def _match_gemini(text: str) -> str:
    text = text.strip().lower()
    if "flash" in text:
        return "gemini-2.5-flash"
    for m in prefs.GEMINI_MODELS:
        if text in m:
            return m
    return "gemini-2.5-pro"


def _resolve(text: str) -> tuple[str | None, str | None, str | None]:
    """→ (engine, model, error). engine/model None means 'leave unchanged'."""
    text = (text or "").strip().lower()
    if not text:
        return None, None, None
    parts = text.split()

    # Explicit "<engine> [model]".
    if parts[0] in ("claude", "codex", "gemini", "auto"):
        engine = parts[0]
        model = " ".join(parts[1:]).strip() or None
        if engine == "codex" and model:
            model = _match_codex(model) or model
        if engine == "gemini" and model:
            model = _match_gemini(model)
        return engine, model, None

    if text in _CLAUDE_ALIASES:
        return "claude", text, None

    cm = _match_codex(text)
    if cm:
        return "codex", cm, None

    if "gemini" in text or "flash" in text or text in ("pro",):
        return "gemini", _match_gemini(text), None

    return None, None, f"Couldn't match '{text}' to an engine/model."


def _status() -> str:
    engine = prefs.get_coding_engine()
    models = prefs.get_coding_models()
    backups = prefs.get_backup_models()
    presets = prefs.model_presets()
    lines = [f"Coding engine: {engine}"]
    for e in prefs.MODEL_ENGINES:
        cur = models.get(e) or "(default)"
        bak = backups.get(e) or "(none)"
        lines.append(f"  {e}: {cur}  · backup: {bak}   options: {', '.join(presets[e])}")
    return "\n".join(lines)


class ModelSwitchSkill(Skill):
    name = "model"
    description = "Switch the coding engine/model (opus, sonnet, codex gpt-5.6-sol, …)"
    final_output = True
    agent_doc = (
        "Switch which engine/model does the coding. args: an engine "
        '("claude"|"codex"|"gemini"|"auto"), a Claude alias ("opus"|"sonnet"|"haiku"), '
        'a codex model ("sol"|"terra"|"gpt-5.5" …), or "<engine> <model>". '
        'Add the word "backup" to set the fallback model instead of the primary '
        '(used automatically if the primary fails). '
        'Examples: "switch to opus"->"opus", "use sonnet"->"sonnet", '
        '"use codex"->"codex", "use sol in codex"->"codex sol", '
        '"set codex backup to gpt-5.5"->"codex backup gpt-5.5", '
        '"backup sonnet"->"backup sonnet". '
        'Empty arg shows the current selection.'
    )

    async def run(self, prompt: str = "", **kwargs) -> str:
        # "backup" anywhere in the message → set the fallback model, not the primary.
        is_backup = "backup" in prompt.lower().split()
        if is_backup:
            prompt = " ".join(w for w in prompt.split() if w.lower() != "backup")

        engine, model, err = _resolve(prompt)
        if engine is None and model is None:
            return (err + "\n\n" + _status()) if err else _status()

        target = (engine if engine and engine != "auto"
                  else prefs.get_coding_engine())

        if is_backup:
            if model and target in prefs.MODEL_ENGINES:
                prefs.set_backup_model(target, model)
                return f"Backup set: {target} backup → {model}"
            return "Tell me the backup model, e.g. 'codex backup gpt-5.5'."

        done = []
        if engine is not None:
            prefs.set_coding_engine(engine)
            done.append(f"engine → {engine}")
        if model and target in prefs.MODEL_ENGINES:
            prefs.set_coding_model(target, model)
            done.append(f"{target} model → {model}")
        return "Coding set: " + ", ".join(done) if done else _status()


register(ModelSwitchSkill())
