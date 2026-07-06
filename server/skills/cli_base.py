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

    # Session reuse: when True, run() reuses one CLI session per (workspace,
    # engine) so turns build on each other and the thread is resumable on the
    # Mac. Two id patterns are supported:
    #   • CLI-assigned (claude, codex): the CLI mints the id; we read it from the
    #     output via extract_session_id() and store it. First call: no resume.
    #   • client-assigned (gemini): we mint the id via new_session_id() and pass
    #     it on the first call; resume by that id. extract_session_id returns None.
    supports_sessions: bool = False

    @property
    def session_engine(self) -> str:
        """Key under which this skill's sessions are stored (defaults to the CLI)."""
        return self.cli_name

    def new_session_id(self) -> str | None:
        """For client-assigned engines (gemini): return a fresh id to open a new
        session with. CLI-assigned engines (claude/codex) return None and let the
        CLI mint it."""
        return None

    def build_command(self, prompt: str, resume_id: str | None = None,
                      new_id: str | None = None) -> list[str]:
        """Return the full argv to spawn. Subclasses must override.
        `resume_id` — continue this stored session (mutually exclusive with new_id).
        `new_id`    — open a new session with this client-minted id (gemini)."""
        raise NotImplementedError

    def parse_output(self, stdout: str, stderr: str) -> str:
        """Convert raw stdout/stderr into the user-facing string. Default: stdout."""
        return stdout.strip()

    def extract_session_id(self, stdout: str) -> str | None:
        """Pull the CLI-assigned session id out of the output so we can resume it
        next time. Client-assigned engines return None (we already know the id)."""
        return None

    def _sync_context(self) -> None:
        """Converge AGENTS.md ↔ CLAUDE.md ↔ GEMINI.md so whichever engine runs
        reads — and leaves — the same shared context. Best-effort; never raises."""
        if not getattr(config, "CONTEXT_AUTO_SYNC", True):
            return
        try:
            from server.skills.context import sync_context_files
            sync_context_files(config.WORKSPACE_DIR)
        except Exception:
            pass

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

        # Read-side guard: converge the shared context BEFORE this engine acts, so
        # a model taking over from a different one reads the latest notes — not a
        # stale mirror. (The post-run sync below handles the write side.)
        self._sync_context()

        # Resume this project's session for this engine, if we have one.
        resume_id = (cli_sessions_store.get(cwd, self.session_engine)
                     if self.supports_sessions else None)

        def _fresh():
            """A brand-new session (no resume). Returns (cmd, client_minted_id)."""
            nid = self.new_session_id() if self.supports_sessions else None
            return self.build_command(prompt, resume_id=None, new_id=nid), nid

        if resume_id:
            cmd, new_id = self.build_command(prompt, resume_id=resume_id), None
        else:
            cmd, new_id = _fresh()
        rc, stdout, stderr = await self._spawn(cmd, cwd)

        # A stored session can go stale (deleted, or the CLI rejects the id). If a
        # resume attempt failed, forget it and retry once with a fresh session so
        # the user never gets stuck behind a dead id.
        if rc not in (0, None) and resume_id:
            cli_sessions_store.clear(cwd, self.session_engine)
            resume_id = None
            cmd, new_id = _fresh()
            rc, stdout, stderr = await self._spawn(cmd, cwd)

        if rc is None:
            return f"[{self.name}] Timed out after {self.timeout}s"

        if rc != 0:
            return (f"[{self.name} error code {rc}]\n"
                    f"{stderr.strip() or stdout.strip() or '(no output)'}")

        # Remember the session id so the next call in this project continues it.
        # CLI-assigned engines report it in output; client-assigned ones already
        # know it (new_id); on a plain resume we just keep the id we resumed.
        if self.supports_sessions:
            sid = self.extract_session_id(stdout) or new_id or resume_id
            if sid:
                cli_sessions_store.set(cwd, self.session_engine, sid)

        # Write-side sync: this engine may have edited its own context file —
        # converge them so the next engine (or the Mac) sees the update.
        self._sync_context()

        return self.parse_output(stdout, stderr)
