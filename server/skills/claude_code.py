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

    def build_command(self, prompt: str) -> list[str]:
        return [
            "claude",
            "-p", prompt,
            "--output-format", "json",
            "--permission-mode", "bypassPermissions",
        ]

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
