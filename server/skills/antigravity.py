import json
import uuid
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
    # Gemini is client-assigned: we mint the id and open the session with
    # --session-id, then continue it with --resume <id> (it rejects reusing
    # --session-id for an existing session).
    supports_sessions = True

    def new_session_id(self) -> str:
        return str(uuid.uuid4())

    def build_command(self, prompt: str, resume_id: str | None = None,
                      new_id: str | None = None, model: str = "") -> list[str]:
        cmd = [
            "gemini",
            "-p", prompt,
            "--yolo",
            "--output-format", "json",
            "--skip-trust",
        ]
        if model:  # resolved by cli_base (primary/backup); '' = CLI default
            cmd += ["-m", model]
        if resume_id:
            cmd += ["--resume", resume_id]
        elif new_id:
            cmd += ["--session-id", new_id]
        return cmd

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

    def extract_usage(self, stdout: str) -> tuple[int, int]:
        brace = stdout.find("{")
        if brace == -1:
            return 0, 0
        try:
            models = (json.loads(stdout[brace:]).get("stats") or {}).get("models") or {}
        except (json.JSONDecodeError, AttributeError):
            return 0, 0
        total = billable = 0
        for stats in models.values():
            tokens = stats.get("tokens") or {}
            inp = tokens.get("input") or 0
            out = tokens.get("candidates") or 0
            cached = tokens.get("cached") or 0
            thoughts = tokens.get("thoughts") or 0
            total += inp + out + cached + thoughts
            billable += inp + out + thoughts
        return total, billable


register(AntigravitySkill())
