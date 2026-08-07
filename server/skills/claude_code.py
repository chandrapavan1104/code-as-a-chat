import json
from server.skills.cli_base import CLISubprocessSkill
from server.skills import register


class ClaudeCodeSkill(CLISubprocessSkill):
    name = "claude"
    description = "Claude Code CLI — multi-file refactors, test runs, git ops"
    agent_doc = ("Heavy coding agent. Use for: code reading/writing, multi-file "
                 "refactors, running tests, git ops, debugging, anything that "
                 "needs to touch the filesystem. ALSO your eyes — it can read "
                 "images (screenshots, error dialogs), PDFs, and any file: pass "
                 "'Read the image/file at <path> and <question>'.")
    cli_name = "claude"
    install_hint = "Install: npm install -g @anthropic-ai/claude-code"
    # Reuse one Claude session per project so turns build on each other and you
    # can `claude --resume <id>` the same thread on the Mac.
    supports_sessions = True

    def build_command(self, prompt: str, resume_id: str | None = None,
                      new_id: str | None = None, model: str = "") -> list[str]:
        cmd = [
            "claude",
            "-p", prompt,
            "--output-format", "json",
            "--permission-mode", "bypassPermissions",
        ]
        if model:  # e.g. opus / sonnet / haiku — resolved by cli_base (primary/backup)
            cmd += ["--model", model]
        if resume_id:  # claude mints its own id; we only resume, never set new_id
            cmd += ["--resume", resume_id]
        return cmd

    def extract_session_id(self, stdout: str) -> str | None:
        try:
            return json.loads(stdout).get("session_id")
        except (json.JSONDecodeError, AttributeError):
            return None

    def extract_usage(self, stdout: str) -> tuple[int, int]:
        try:
            usage = json.loads(stdout).get("usage") or {}
        except (json.JSONDecodeError, AttributeError):
            return 0, 0
        inp = usage.get("input_tokens") or 0
        out = usage.get("output_tokens") or 0
        created = usage.get("cache_creation_input_tokens") or 0
        read = usage.get("cache_read_input_tokens") or 0
        return inp + out + created + read, inp + out + created

    def parse_output(self, stdout: str, stderr: str) -> str:
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            return stdout.strip()

        result = (data.get("result") or "").strip()
        if not result:
            return f"[claude] (no result field)\n{stdout[:500]}"

        usage = data.get("usage") or {}
        cost = data.get("total_cost_usd")
        footer = []
        if usage.get("input_tokens") or usage.get("output_tokens"):
            footer.append(
                f"tokens in={usage.get('input_tokens', 0)} out={usage.get('output_tokens', 0)}"
            )
        if cost is not None:
            footer.append(f"cost ~${cost:.4f}")
        if footer:
            result += "\n\n— " + " | ".join(footer)
        return result


register(ClaudeCodeSkill())
