"""Bounded, isolated coding run for one Night Shift job.

Deliberately does NOT go through the session-coupled `CLISubprocessSkill`: those
read/write `config.WORKSPACE_DIR` and each project's interactive session pointer
(codex's `build_command` even hardcodes the global workspace). A night job is a
fresh agent on a throwaway branch — it must never resume or fork the threads you
talk to from the phone. So we build the argv here and run in the job's own repo
via the subprocess `cwd`, passing no resume id.

Output parsing + token accounting reuse the registered skills' own
`parse_output` / `extract_usage` so a night run is measured exactly like an
interactive one.
"""

import asyncio

from server import config

# Engine → the CLI's own skill name (for reusing parse_output / extract_usage).
_PARSE_SKILL = {"claude": "claude", "codex": "codex", "gemini": "antigravity"}

NIGHT_SYSTEM = """You are an autonomous build agent working overnight on ONE task
in THIS repository. Work ONLY inside this repo.

1. Implement the task with the SMALLEST correct change set. Match the surrounding
   code's style and conventions. Do NOT refactor, rename, reformat, or "improve"
   unrelated code. Add no new dependencies unless strictly required.
2. If the project has its own tooling (a venv, linter, tests), use it to verify
   your change compiles / passes. Fix what you broke.
3. Do NOT commit, push, switch branches, restart anything, or build/deploy — the
   night runner handles the branch, build, and review. Just leave the working
   tree with your change in it.
4. Never invoke launchctl, pkill, killall, scripts/codechat, or any deployment /
   restart helper. Those commands can stop the supervisor that owns this job.

STOP and change NOTHING (explain instead) if ANY of these hold:
- The task is a design or product decision that needs the owner to choose a
  direction (naming, UX, architecture, scope).
- The requirements are ambiguous, or a correct fix would be large/speculative.
- It isn't really a code task (an environment/account/config problem).
In that case make no edits and clearly state: what you understood, why you
stopped, and exactly what decision or information you need. This is the right
outcome for anything meant to be decided by a human — leave it for them.

BE HONEST about confidence. If you cannot run or reproduce the result, say the
change is UNVERIFIED and name what would confirm it. End with a 2-4 line summary:
what the task was, what you changed (files), and any caveat. Be concise."""

RESEARCH_SYSTEM = """You are an autonomous research agent working on ONE bounded
task. Your deliverable is a factual report, not a code change.

Use public web research/search capabilities available to you. Cross-check claims
and include direct source URLs with each useful finding. Clearly separate facts,
inferences, and recommendations. Never fabricate a source, company, person, or
contact detail.

READ-ONLY SAFETY: do not contact anyone; do not send email/messages, submit forms,
log in, purchase, register, or change any external/local state. If the task asks
for outreach, provide a proposed strategy/template only and state that no outreach
was performed. End with a concise findings summary and practical next steps."""


def _argv(engine: str, repo: str, prompt: str, model: str) -> list[str]:
    if engine == "claude":
        cmd = ["claude", "-p", prompt, "--output-format", "json",
               "--permission-mode", "bypassPermissions"]
        if model:
            cmd += ["--model", model]
        return cmd
    if engine == "codex":
        return ["codex", "exec", "--json", "--skip-git-repo-check",
                "--dangerously-bypass-approvals-and-sandbox", "-C", repo,
                "--model", model or config.CODEX_MODEL, prompt]
    if engine == "gemini":
        cmd = ["gemini", "-p", prompt, "--yolo", "--output-format", "json",
               "--skip-trust"]
        if model:
            cmd += ["-m", model]
        return cmd
    raise ValueError(f"unknown night engine {engine!r}")


def _parse(engine: str, stdout: str, stderr: str) -> tuple[str, int, int]:
    """(final_text, total_tokens, billable_tokens) via the engine's own skill."""
    from server.skills import get_skill
    skill = get_skill(_PARSE_SKILL.get(engine, engine))
    # parse_output / extract_usage live on the CLI skill subclasses (not the base
    # Skill), so reach them dynamically and fall back if a skill lacks them.
    parse = getattr(skill, "parse_output", None)
    usage = getattr(skill, "extract_usage", None)
    try:
        text = parse(stdout, stderr) if parse else stdout.strip()
    except Exception:
        text = stdout.strip()
    try:
        total, billable = usage(stdout) if usage else (0, 0)
    except Exception:
        total, billable = 0, 0
    return text, total, billable


def _model_for(engine: str) -> str:
    from server import prefs
    return prefs.get_coding_model(engine) or ""


def _report_stdout(callback, output: bytes) -> None:
    if callback is None:
        return
    try:
        callback(output.decode(errors="replace"))
    except Exception:
        pass


async def run_job(engine: str, repo: str, task: str, timeout: int,
                  on_spawn=None, on_stdout=None) -> tuple[str, int, int, str | None]:
    """Run one bounded night build. Returns (final_text, total_tok, billable_tok,
    error). `error` is a short string on timeout / spawn failure, else None.

    `on_spawn(proc)` — if given, called with the live subprocess right after it
    starts, so the runner can kill it on a stop request.
    `on_stdout(text)` receives the buffered raw stdout once the attempt exits.
    """
    prompt = f"{NIGHT_SYSTEM}\n\n=== TASK ===\n{task}"
    argv = _argv(engine, repo, prompt, _model_for(engine))

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=repo,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        if on_spawn is not None:
            try:
                on_spawn(proc)
            except Exception:
                pass
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        out_b = b""
        if proc is not None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                out_b, _ = await proc.communicate()
            except Exception:
                pass
        _report_stdout(on_stdout, out_b)
        return "", 0, 0, f"ran past the {timeout}s limit and was stopped"
    except FileNotFoundError:
        return "", 0, 0, f"the {engine} CLI is not installed on PATH"
    except Exception as exc:  # noqa: BLE001 — surface any spawn failure as job error
        return "", 0, 0, f"failed to launch {engine}: {exc}"

    stdout = out_b.decode(errors="replace")
    stderr = err_b.decode(errors="replace")
    _report_stdout(on_stdout, out_b)
    text, total, billable = _parse(engine, stdout, stderr)

    error = None
    if proc.returncode not in (0, None):
        error = (stderr.strip() or text.strip() or f"exit code {proc.returncode}")[:400]
    return text, total, billable, error


async def run_research_job(engine: str, cwd: str, task: str, timeout: int,
                           on_spawn=None, on_stdout=None) -> tuple[str, int, int, str | None]:
    """Run a read-only research job without Git/branch expectations."""
    prompt = f"{RESEARCH_SYSTEM}\n\n=== RESEARCH TASK ===\n{task}"
    argv = _argv(engine, cwd, prompt, _model_for(engine))
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        if on_spawn is not None:
            on_spawn(proc)
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        out_b = b""
        if proc is not None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                out_b, _ = await proc.communicate()
            except Exception:
                pass
        _report_stdout(on_stdout, out_b)
        return "", 0, 0, f"ran past the {timeout}s limit and was stopped"
    except FileNotFoundError:
        return "", 0, 0, f"the {engine} CLI is not installed on PATH"
    except Exception as exc:  # noqa: BLE001
        return "", 0, 0, f"failed to launch {engine}: {exc}"

    stdout = out_b.decode(errors="replace")
    stderr = err_b.decode(errors="replace")
    _report_stdout(on_stdout, out_b)
    text, total, billable = _parse(engine, stdout, stderr)
    error = None
    if proc.returncode not in (0, None):
        error = (stderr.strip() or text.strip() or f"exit code {proc.returncode}")[:400]
    return text, total, billable, error
