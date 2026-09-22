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
import json
from dataclasses import dataclass
from pathlib import Path

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

Return ONLY one JSON object: {"status":"report_complete"|"waiting_input"|"blocked"|"unverified","report":"...","question":"...","reason":"..."}.
Use report_complete only when useful findings include source URLs. Use unverified
when findings cannot be adequately sourced; use waiting_input for a user decision
and blocked for an external/access/tool blocker. Never turn an error into a report.

READ-ONLY SAFETY: do not contact anyone; do not send email/messages, submit forms,
log in, purchase, register, or change any external/local state. If the task asks
for outreach, provide a proposed strategy/template only and state that no outreach
was performed. End with a concise findings summary and practical next steps."""


@dataclass(frozen=True)
class ResearchOutcome:
    status: str
    report: str = ""
    question: str = ""
    reason: str = ""


def _has_source(report: str) -> bool:
    return "http://" in report.lower() or "https://" in report.lower()


def _mentions_missing_attachment(report: str) -> bool:
    lower = report.lower()
    return any(marker in lower for marker in (
        "missing image", "image unavailable", "attachment unavailable",
        "could not read the provided", "couldn't read the provided",
        "could not access the provided", "file was not provided",
    ))


def parse_research_outcome(text: str) -> ResearchOutcome:
    """Parse the research protocol conservatively, including legacy output."""
    raw = (text or "").strip()
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        value = None
    if isinstance(value, dict):
        status = str(value.get("status") or "").strip().lower()
        report = str(value.get("report") or "").strip()
        question = str(value.get("question") or "").strip()
        reason = str(value.get("reason") or "").strip()
        if status in {"report_complete", "waiting_input", "blocked", "unverified"}:
            if status == "report_complete" and _mentions_missing_attachment(report):
                return ResearchOutcome("blocked", report, reason="required attachment was unavailable")
            if status == "report_complete" and (not report or not _has_source(report)):
                return ResearchOutcome("unverified", report, reason="report has no source URL")
            return ResearchOutcome(status, report, question, reason)
    lower = raw.lower()
    if any(marker in lower for marker in ("need your input", "waiting for your", "decision needed")):
        return ResearchOutcome("waiting_input", question=raw)
    if any(marker in lower for marker in ("blocked", "could not access", "cannot access", "tool error")):
        return ResearchOutcome("blocked", reason=raw)
    if any(marker in lower for marker in ("unverified", "not verified", "insufficient evidence")):
        return ResearchOutcome("unverified", report=raw, reason="legacy output marked unverified")
    if raw and _mentions_missing_attachment(raw):
        return ResearchOutcome("blocked", report=raw, reason="required attachment was unavailable")
    if raw and _has_source(raw):
        return ResearchOutcome("report_complete", report=raw)
    return ResearchOutcome("unverified", report=raw, reason="legacy output lacks source URLs")


def attachment_handoff(job: dict) -> tuple[list[str], list[str]]:
    """Validate persisted refs immediately before handing them to an agent."""
    from server.media import is_served_path
    refs = (job.get("spec_json") or {}).get("attachment_refs") or []
    valid, missing = [], []
    for value in refs:
        path = Path(str(value)).expanduser()
        if is_served_path(path):
            valid.append(str(path.resolve()))
        else:
            missing.append(str(value))
    return valid, missing


def task_with_attachments(job: dict) -> tuple[str, str | None]:
    """Build the worker prompt using only validated existing attachment paths."""
    task = job.get("task") or ""
    valid, missing = attachment_handoff(job)
    if missing:
        return task, "Required attachment is unavailable: " + ", ".join(missing[:4])
    if valid:
        task += "\n\n=== PROVIDED ATTACHMENTS ===\n" + "\n".join(
            f"Read this provided file: {path}" for path in valid)
    return task, None


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


async def run_job(engine: str, repo: str, task: str, timeout: int,
                  on_spawn=None) -> tuple[str, int, int, str | None]:
    """Run one bounded night build. Returns (final_text, total_tok, billable_tok,
    error). `error` is a short string on timeout / spawn failure, else None.

    `on_spawn(proc)` — if given, called with the live subprocess right after it
    starts, so the runner can kill it on a stop request.
    """
    prompt = f"{NIGHT_SYSTEM}\n\n=== TASK ===\n{task}"
    argv = _argv(engine, repo, prompt, _model_for(engine))

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=repo,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        if on_spawn is not None:
            try:
                on_spawn(proc)
            except Exception:
                pass
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        if proc is not None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await proc.communicate()
            except Exception:
                pass
        return "", 0, 0, f"ran past the {timeout}s limit and was stopped"
    except FileNotFoundError:
        return "", 0, 0, f"the {engine} CLI is not installed on PATH"
    except Exception as exc:  # noqa: BLE001 — surface any spawn failure as job error
        return "", 0, 0, f"failed to launch {engine}: {exc}"

    stdout = out_b.decode(errors="replace")
    stderr = err_b.decode(errors="replace")
    text, total, billable = _parse(engine, stdout, stderr)

    error = None
    if proc.returncode not in (0, None):
        error = (stderr.strip() or text.strip() or f"exit code {proc.returncode}")[:400]
    return text, total, billable, error


async def run_research_job(engine: str, cwd: str, task: str, timeout: int,
                           on_spawn=None) -> tuple[str, int, int, str | None]:
    """Run a read-only research job without Git/branch expectations."""
    prompt = f"{RESEARCH_SYSTEM}\n\n=== RESEARCH TASK ===\n{task}"
    argv = _argv(engine, cwd, prompt, _model_for(engine))
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        if on_spawn is not None:
            on_spawn(proc)
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        if proc is not None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await proc.communicate()
            except Exception:
                pass
        return "", 0, 0, f"ran past the {timeout}s limit and was stopped"
    except FileNotFoundError:
        return "", 0, 0, f"the {engine} CLI is not installed on PATH"
    except Exception as exc:  # noqa: BLE001
        return "", 0, 0, f"failed to launch {engine}: {exc}"

    stdout = out_b.decode(errors="replace")
    stderr = err_b.decode(errors="replace")
    text, total, billable = _parse(engine, stdout, stderr)
    error = None
    if proc.returncode not in (0, None):
        error = (stderr.strip() or text.strip() or f"exit code {proc.returncode}")[:400]
    return text, total, billable, error
