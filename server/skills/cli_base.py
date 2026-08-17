import asyncio
import shutil
import time
from server.skills.base import Skill
from server import config
from server import workspace
from server.db import cli_runs_store, cli_sessions_store, native_sessions


class CLISubprocessSkill(Skill):
    """Base for skills that delegate to a CLI tool via subprocess."""

    cli_name: str         # binary to look up on PATH (e.g. "claude")
    install_hint: str     # message shown if the CLI is missing
    timeout: int = 600    # heavy coding/build turns need the same 10 min as Codex

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
                      new_id: str | None = None, model: str = "") -> list[str]:
        """Return the full argv to spawn. Subclasses must override.
        `resume_id` — continue this stored session (mutually exclusive with new_id).
        `new_id`    — open a new session with this client-minted id (gemini).
        `model`     — concrete model to run ('' = the CLI's own default)."""
        raise NotImplementedError

    def _default_model(self) -> str:
        """Model to use when nothing is pinned ('' = the CLI's built-in default).
        Codex overrides this to config.CODEX_MODEL."""
        return ""

    def _primary_model(self) -> str:
        from server import prefs
        return prefs.get_coding_model(self.session_engine) or self._default_model()

    def _backup_model(self) -> str:
        from server import prefs
        return prefs.get_backup_model(self.session_engine)

    def _failed(self, rc: int | None, stdout: str) -> bool:
        """Did this run fail (→ worth trying the backup model)? Default: the
        process exited non-zero. Codex overrides to also catch a failed turn that
        still exits 0."""
        return rc not in (0, None)

    @staticmethod
    def _resume_rejected(stdout: str, stderr: str) -> bool:
        """True only when the CLI says the session id itself is unusable.

        A generic model/auth/quota failure must not be mistaken for a stale
        thread: doing so silently forks the project's conversation.
        """
        text = f"{stdout}\n{stderr}".lower()
        return any(marker in text for marker in (
            "session not found", "session does not exist", "invalid session",
            "unknown session", "conversation not found", "no conversation found",
            "could not find session", "failed to resume",
        ))

    def parse_output(self, stdout: str, stderr: str) -> str:
        """Convert raw stdout/stderr into the user-facing string. Default: stdout."""
        return stdout.strip()

    def extract_session_id(self, stdout: str) -> str | None:
        """Pull the CLI-assigned session id out of the output so we can resume it
        next time. Client-assigned engines return None (we already know the id)."""
        return None

    def extract_usage(self, stdout: str) -> tuple[int, int]:
        """Return (all tokens, billable tokens) for one CLI attempt."""
        return 0, 0

    @staticmethod
    def _source(session_id: str | None) -> str:
        if session_id and session_id.startswith("app:"):
            return "gajala"
        if session_id and session_id.startswith("tg:"):
            return "telegram"
        return "server"

    def _record_attempt(self, cwd: str, source: str, started_at: float,
                        rc: int | None, stdout: str,
                        session_id: str | None) -> None:
        try:
            total, billable = self.extract_usage(stdout)
            status = ("timeout" if rc is None
                      else ("error" if self._failed(rc, stdout) else "success"))
            cli_runs_store.add(
                workspace=cwd, engine=self.session_engine,
                cli_session_id=session_id or self.extract_session_id(stdout),
                source=source, started_at=started_at, status=status,
                total_tokens=total, billable_tokens=billable,
            )
        except Exception:
            pass

    def _sync_context(self) -> None:
        """Converge AGENTS.md ↔ CLAUDE.md ↔ GEMINI.md so whichever engine runs
        reads — and leaves — the same shared context. Best-effort; never raises."""
        if not getattr(config, "CONTEXT_AUTO_SYNC", True):
            return
        try:
            from server.skills.context import sync_context_files
            sync_context_files(workspace.active())
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

    def _resume_id(self, cwd: str) -> str | None:
        """Which session should this project+engine continue?

        The rule is "the folder's most recent thread, whoever opened it". Our own
        pointer only sees turns that came through the server — a session you open
        by running the CLI yourself on the Mac never touches it, so trusting the
        pointer alone forks the thread. Asking the CLI's own store instead makes
        both sides resolve to the same session, which is the whole guarantee.

        The pointer stays as the fallback for when native discovery finds nothing
        (unreadable store, or an engine whose layout we don't parse).
        """
        stored = cli_sessions_store.get(cwd, self.session_engine)
        if not getattr(config, "SESSION_FOLLOW_NATIVE", True):
            return stored

        native = native_sessions.latest(cwd, self.session_engine)
        if native is None:
            return stored
        native_id = native[0]
        if native_id != stored:
            # The Mac (or another caller) moved on — follow it, don't fork.
            cli_sessions_store.set(cwd, self.session_engine, native_id)
        return native_id

    async def _attempt(self, prompt: str, cwd: str, model: str,
                       source: str = "server"):
        """One model's run: resume the stored session if any, and if that resume
        fails, retry once fresh. Returns (rc, stdout, stderr, new_id, resume_id).
        A backup model resumes the same thread: models are replaceable workers,
        while the project+engine thread is the durable conversation."""
        resume_id = self._resume_id(cwd) if self.supports_sessions else None

        def _fresh():
            nid = self.new_session_id() if self.supports_sessions else None
            return self.build_command(prompt, resume_id=None, new_id=nid, model=model), nid

        if resume_id:
            cmd, new_id = self.build_command(prompt, resume_id=resume_id, model=model), None
        else:
            cmd, new_id = _fresh()
        started_at = time.time()
        rc, stdout, stderr = await self._spawn(cmd, cwd)
        self._record_attempt(cwd, source, started_at, rc, stdout,
                             resume_id or new_id)

        # A stored session can go stale (deleted, or the CLI rejects the id). If a
        # resume attempt failed, forget it and retry once with a fresh session so
        # the user never gets stuck behind a dead id.
        if rc not in (0, None) and resume_id and self._resume_rejected(stdout, stderr):
            cli_sessions_store.clear(cwd, self.session_engine)
            resume_id = None
            cmd, new_id = _fresh()
            started_at = time.time()
            rc, stdout, stderr = await self._spawn(cmd, cwd)
            self._record_attempt(cwd, source, started_at, rc, stdout, new_id)
        return rc, stdout, stderr, new_id, resume_id

    async def run(self, prompt: str = "", **kwargs) -> str:
        prompt = prompt.strip()
        if not prompt:
            return f"Usage: /{self.name} <prompt>"

        if shutil.which(self.cli_name) is None:
            return f"[{self.name}] CLI '{self.cli_name}' not installed.\n{self.install_hint}"

        cwd = str(workspace.active())

        # Read-side guard: converge the shared context BEFORE this engine acts, so
        # a model taking over from a different one reads the latest notes — not a
        # stale mirror. (The post-run sync below handles the write side.)
        self._sync_context()

        primary = self._primary_model()
        backup = self._backup_model()
        source = self._source(kwargs.get("session_id"))

        rc, stdout, stderr, new_id, resume_id = await self._attempt(
            prompt, cwd, primary, source=source)

        # If the primary model failed and a distinct backup is set, retry once on
        # it in the same session. The model may change, but the project's thread
        # must not fork merely because a fallback was needed.
        fell_back = False
        if self._failed(rc, stdout) and backup and backup != primary:
            rc, stdout, stderr, new_id, resume_id = await self._attempt(
                prompt, cwd, backup, source=source)
            fell_back = True

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

        out = self.parse_output(stdout, stderr)
        if fell_back:
            out = (f"⚠️ {primary or 'primary model'} failed — retried on backup "
                   f"{backup}.\n\n{out}")
        return out
