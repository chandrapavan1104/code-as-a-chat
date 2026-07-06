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
    # Reuse one Codex thread per project ("codex exec resume <id>"); the CLI
    # mints the id and reports it in a `thread.started` event.
    supports_sessions = True

    _FLAGS = ["--json", "--skip-git-repo-check",
              "--dangerously-bypass-approvals-and-sandbox"]

    def build_command(self, prompt: str, resume_id: str | None = None,
                      new_id: str | None = None) -> list[str]:
        if resume_id:
            # `codex exec resume <id>` continues the thread; cwd comes from the
            # subprocess (resume has no -C flag).
            return ["codex", "exec", "resume", resume_id, *self._FLAGS, prompt]
        return ["codex", "exec", *self._FLAGS,
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

    def parse_output(self, stdout: str, stderr: str) -> str:
        # Codex emits JSONL: one JSON event per line.
        # Final answer is the last `item.completed` event with type=agent_message.
        # Usage info is in the `turn.completed` event.
        final_text: str | None = None
        usage: dict | None = None

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

        if not final_text:
            return f"[codex] (no agent_message event)\n{stdout[:500]}"

        if usage:
            final_text += (
                f"\n\n— tokens in={usage.get('input_tokens', 0)} "
                f"out={usage.get('output_tokens', 0)}"
            )
        return final_text


register(CodexSkill())
