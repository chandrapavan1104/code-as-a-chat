import asyncio
import shutil
from server.skills.base import Skill
from server import config
from server.db import cli_sessions_store


class CLISubprocessSkill(Skill):
    """Base for skills that delegate to a CLI tool via subprocess."""

    cli_name: str         # binary to look up on PATH (e.g. "claude")
    install_hint: str     # message shown if the CLI is missing
    timeout: int = 300    # seconds before we kill the subprocess

    # Session reuse: when True, run() looks up a stored CLI session for this
    # (workspace, engine) and passes it to build_command(resume_id=...), then
    # captures the session id from the output for next time. Continuity + far
    # less clutter. Engines whose resume story isn't verified leave this False.
    supports_sessions: bool = False

    @property
    def session_engine(self) -> str:
        """Key under which this skill's sessions are stored (defaults to the CLI)."""
        return self.cli_name

    def build_command(self, prompt: str, resume_id: str | None = None) -> list[str]:
        """Return the full argv to spawn. Subclasses must override. `resume_id`
        is set only for session-aware skills that have a stored session."""
        raise NotImplementedError

    def parse_output(self, stdout: str, stderr: str) -> str:
        """Convert raw stdout/stderr into the user-facing string. Default: stdout."""
        return stdout.strip()

    def extract_session_id(self, stdout: str) -> str | None:
        """Pull the CLI session id out of the output so we can resume it next
        time. Session-aware skills override; default: none."""
        return None

    async def _spawn(self, cmd: list[str], cwd: str) -> tuple[int | None, str, str]:
        """Run one subprocess; returns (returncode, stdout, stderr). Timeout →
        returncode None with a marker in stderr."""
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
            return None, "", "__timeout__"
        return (proc.returncode,
                stdout_b.decode(errors="replace"),
                stderr_b.decode(errors="replace"))

    async def run(self, prompt: str = "", **kwargs) -> str:
        prompt = prompt.strip()
        if not prompt:
            return f"Usage: /{self.name} <prompt>"

        if shutil.which(self.cli_name) is None:
            return f"[{self.name}] CLI '{self.cli_name}' not installed.\n{self.install_hint}"

        cwd = str(config.WORKSPACE_DIR)

        # Resume this project's session for this engine, if we have one.
        resume_id = (cli_sessions_store.get(cwd, self.session_engine)
                     if self.supports_sessions else None)
        cmd = self.build_command(prompt, resume_id=resume_id)
        rc, stdout, stderr = await self._spawn(cmd, cwd)

        # A stored session can go stale (deleted, or the CLI rejects the id). If a
        # resume attempt failed, forget it and retry once with a fresh session so
        # the user never gets stuck behind a dead id.
        if rc not in (0, None) and resume_id:
            cli_sessions_store.clear(cwd, self.session_engine)
            cmd = self.build_command(prompt, resume_id=None)
            rc, stdout, stderr = await self._spawn(cmd, cwd)

        if rc is None:
            return f"[{self.name}] Timed out after {self.timeout}s"

        if rc != 0:
            return (f"[{self.name} error code {rc}]\n"
                    f"{stderr.strip() or stdout.strip() or '(no output)'}")

        # Remember the session id so the next call in this project continues it.
        if self.supports_sessions:
            sid = self.extract_session_id(stdout)
            if sid:
                cli_sessions_store.set(cwd, self.session_engine, sid)

        # An agent may have edited its own context file (CLAUDE.md / AGENTS.md /
        # GEMINI.md). Converge them so all engines stay on the same context.
        if getattr(config, "CONTEXT_AUTO_SYNC", True):
            try:
                from server.skills.context import sync_context_files
                sync_context_files(config.WORKSPACE_DIR)
            except Exception:
                pass  # never let context sync break a real result

        return self.parse_output(stdout, stderr)
