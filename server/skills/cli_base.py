import asyncio
import shutil
from server.skills.base import Skill
from server import config


class CLISubprocessSkill(Skill):
    """Base for skills that delegate to a CLI tool via subprocess."""

    cli_name: str         # binary to look up on PATH (e.g. "claude")
    install_hint: str     # message shown if the CLI is missing
    timeout: int = 300    # seconds before we kill the subprocess

    def build_command(self, prompt: str) -> list[str]:
        """Return the full argv to spawn. Subclasses must override."""
        raise NotImplementedError

    def parse_output(self, stdout: str, stderr: str) -> str:
        """Convert raw stdout/stderr into the user-facing string. Default: stdout."""
        return stdout.strip()

    async def run(self, prompt: str = "", **kwargs) -> str:
        prompt = prompt.strip()
        if not prompt:
            return f"Usage: /{self.name} <prompt>"

        if shutil.which(self.cli_name) is None:
            return f"[{self.name}] CLI '{self.cli_name}' not installed.\n{self.install_hint}"

        cwd = str(config.WORKSPACE_DIR)
        cmd = self.build_command(prompt)

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout
            )
        except asyncio.TimeoutError:
            if proc is not None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            return f"[{self.name}] Timed out after {self.timeout}s"

        stdout = stdout_b.decode(errors="replace")
        stderr = stderr_b.decode(errors="replace")

        if proc.returncode != 0:
            return (f"[{self.name} error code {proc.returncode}]\n"
                    f"{stderr.strip() or stdout.strip() or '(no output)'}")

        # An agent may have edited its own context file (CLAUDE.md / AGENTS.md /
        # GEMINI.md). Converge them so all engines stay on the same context.
        if getattr(config, "CONTEXT_AUTO_SYNC", True):
            try:
                from server.skills.context import sync_context_files
                sync_context_files(config.WORKSPACE_DIR)
            except Exception:
                pass  # never let context sync break a real result

        return self.parse_output(stdout, stderr)
