import json
from server.skills.cli_base import CLISubprocessSkill
from server.skills import register


class AntigravitySkill(CLISubprocessSkill):
    name = "antigravity"
    command = "gemini"
    description = "Gemini CLI — code analysis and parallel subagent orchestration"
    agent_doc = ('Google Gemini agent. Pick when the user explicitly says '
                 '"gemini" or "google".')
    cli_name = "gemini"
    install_hint = "Install: npm install -g @google/gemini-cli"

    def build_command(self, prompt: str) -> list[str]:
        return [
            "gemini",
            "-p", prompt,
            "--yolo",
            "--output-format", "json",
            "--skip-trust",
        ]

    def parse_output(self, stdout: str, stderr: str) -> str:
        # Gemini prints warnings before the JSON body. Locate the first `{`.
        brace = stdout.find("{")
        if brace == -1:
            return stdout.strip() or "[gemini] (empty response)"

        try:
            data = json.loads(stdout[brace:])
        except json.JSONDecodeError:
            return stdout[brace:].strip()

        response = (data.get("response") or "").strip()
        if not response:
            return f"[gemini] (no response field)\n{stdout[brace:][:500]}"

        # Token footer — Gemini nests stats per-model
        stats = data.get("stats", {}).get("models", {})
        total_in = total_out = 0
        for model_stats in stats.values():
            toks = model_stats.get("tokens", {}) or {}
            total_in += toks.get("input", 0)
            total_out += toks.get("candidates", 0)
        if total_in or total_out:
            response += f"\n\n— tokens in={total_in} out={total_out}"
        return response


register(AntigravitySkill())
