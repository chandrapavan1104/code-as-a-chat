import json
from server import config
from server.skills.cli_base import CLISubprocessSkill
from server.skills import register


class CodexSkill(CLISubprocessSkill):
    name = "codex"
    description = "OpenAI Codex CLI — code generation with filesystem access"
    agent_doc = ('OpenAI coding agent. Same capability as claude. Pick when the '
                 'user explicitly says "codex" or "openai".')
    cli_name = "codex"
    install_hint = "Install: npm install -g @openai/codex"
    timeout = 600
    # Reuse one Codex thread per project ("codex exec resume <id>"); the CLI
    # mints the id and reports it in a `thread.started` event.
    supports_sessions = True

    _FLAGS = ["--json", "--skip-git-repo-check",
              "--dangerously-bypass-approvals-and-sandbox"]

    def _default_model(self) -> str:
        return config.CODEX_MODEL

    def _failed(self, rc: int | None, stdout: str) -> bool:
        # codex exec can print an error / turn.failed event and still exit 0.
        if rc not in (0, None):
            return True
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                if json.loads(line).get("type") in ("error", "turn.failed"):
                    return True
            except json.JSONDecodeError:
                continue
        return False

    def build_command(self, prompt: str, resume_id: str | None = None,
                      new_id: str | None = None, model: str = "") -> list[str]:
        model_flags = ["--model", model or config.CODEX_MODEL]
        if resume_id:
            # `codex exec resume <id>` continues the thread; cwd comes from the
            # subprocess (resume has no -C flag).
            return ["codex", "exec", "resume", resume_id, *self._FLAGS,
                    *model_flags, prompt]
        return ["codex", "exec", *self._FLAGS, *model_flags,
                "-C", str(config.WORKSPACE_DIR), prompt]

    def extract_session_id(self, stdout: str) -> str | None:
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "thread.started" and event.get("thread_id"):
                return event["thread_id"]
        return None

    def extract_usage(self, stdout: str) -> tuple[int, int]:
        usage = None
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "turn.completed":
                usage = event.get("usage") or {}
        if not usage:
            return 0, 0
        total = usage.get("total_tokens")
        if total is None:
            total = (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
        return total, max(0, total - (usage.get("cached_input_tokens") or 0))

    def parse_output(self, stdout: str, stderr: str) -> str:
        # Codex emits JSONL: one JSON event per line.
        # Final answer is the last `item.completed` event with type=agent_message.
        # Usage info is in the `turn.completed` event.
        final_text: str | None = None
        usage: dict | None = None
        error_text: str | None = None

        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            etype = event.get("type")
            if etype == "item.completed":
                item = event.get("item", {})
                if item.get("type") == "agent_message":
                    final_text = item.get("text", "")
            elif etype == "turn.completed":
                usage = event.get("usage")
            elif etype in ("error", "turn.failed"):
                msg = event.get("message") or (event.get("error") or {}).get("message")
                if msg:
                    error_text = str(msg)

        if not final_text:
            if error_text:
                return _codex_error_hint(error_text, stderr)
            return f"[codex] (no agent_message event)\n{stdout[:500]}"

        if usage:
            final_text += (
                f"\n\n— tokens in={usage.get('input_tokens', 0)} "
                f"out={usage.get('output_tokens', 0)}"
            )
        return final_text


def _codex_error_hint(message: str, stderr: str) -> str:
    """Turn a codex failure into a clean, actionable line. The common one is a
    model/CLI-version skew (the codex desktop app updates its shared model cache
    past the installed CLI), which surfaces as 'requires a newer version' or a
    'models cache' load error — fixable by upgrading the CLI + refreshing cache."""
    low = (message + " " + stderr).lower()
    if ("newer version" in low or "models cache" in low
            or "model metadata" in low or "not found" in low):
        return (f"[codex] {message}\n\n"
                "This is a codex CLI/model version skew. Fix on the Mac:\n"
                "  npm install -g @openai/codex@latest\n"
                "  rm ~/.codex/models_cache.json   # refetched on next run\n"
                "Or pick a model your CLI already supports (e.g. gpt-5.5).")
    return f"[codex] {message}"


register(CodexSkill())
