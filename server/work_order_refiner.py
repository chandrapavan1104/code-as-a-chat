"""Turn a rough queue capture into an executable, reviewable work order."""

from __future__ import annotations

from pathlib import Path

from server.db import night_queue_store
from server.skills.shell import _claude_cli, _parse_json_decision
from server.work_orders import WorkOrderSpec, mark_refined


REFINE_TIMEOUT = 180   # seconds per Claude Sonnet refinement attempt


_SYSTEM = """\
You refine a rough task into a complete worker handoff and classify its type.
Return ONLY one JSON object with these exact fields:
{"work_type":"coding"|"research","title":"...","outcome":"...","context":"...","plan":["..."],
"policy":"...","acceptance":["..."],"test_handoff":"...",
"out_of_scope":"...","assumptions":["..."]}

Rules:
- Preserve the user's intent. Do not silently expand scope.
- Use work_type "coding" only when the requested outcome requires changing
  files in a software repository. Use "research" for investigation, lead lists,
  comparisons, reports, or strategies whose deliverable is information.
- Make a practical, ordered implementation plan with concrete verification.
- Acceptance criteria must be observable.
- test_handoff must explain how the owner can verify from their phone. For web
  work, require a localhost server on its OWN dedicated port, reached at
  https://<mac>.<tailnet>.ts.net:<that port>. For app work, require the
  APK/build and exact screen interactions.
- NEVER take over the default Tailscale route. `tailscale serve` with no port,
  or on 443, repoints https://<mac>.<tailnet>.ts.net itself — which is the
  owner's phone connection to this assistant. A job that grabs it makes every
  screen in the app return 404. Always pass an explicit non-443 port, and say so
  in policy.
- Policy must preserve existing changes, forbid destructive/external actions
  without approval, and keep secrets out of output. It must also forbid changing
  the default Tailscale route, stopping/restarting the codeasachat services, or
  binding to port 8000 — those are the owner's live connection to this
  assistant.
- Research jobs may gather public information and source URLs, but MUST NOT
  contact people, send email/messages, submit forms, log in, purchase, or claim
  an outreach action succeeded. Those actions belong in out_of_scope.
- The TARGET PROJECT names the repository this work runs in. Every reference to
  "this project", "the repo", "the codebase" means THAT project and nothing
  else. Never substitute a different project, and never assume the task is about
  the tooling that is refining it.
- You are NOT shown the target project's files. When the plan depends on their
  contents — a repo name, an existing setup guide, current remotes, framework
  choices — make step 1 "read <the relevant file(s)> in the repo and follow what
  they specify", and record in assumptions that the worker must ground itself in
  the repo rather than in anything guessed here. Do not invent file contents,
  repository names, or project descriptions.
- If the rough request is ambiguous, choose the smallest safe interpretation
  and record every choice in assumptions. Never invent credentials or approval.
- Output valid JSON, no markdown or prose.
"""


def _parse_complete(raw: str) -> WorkOrderSpec | None:
    data = _parse_json_decision(raw)
    if not isinstance(data, dict):
        return None
    try:
        spec = WorkOrderSpec.model_validate(data)
    except Exception:
        return None
    return spec if spec.is_complete else None


async def refine_job(job_id: int, *, allow_cloud: bool = False,
                     instructions: str = "") -> dict:
    if not allow_cloud:
        raise PermissionError(
            "confirm sending this rough task text to Claude Sonnet; "
            "no repo files or paths are included"
        )
    job = night_queue_store.get(job_id)
    if not job:
        raise LookupError(f"no job #{job_id}")
    if job["status"] in ("running", "closed"):
        raise ValueError(f"cannot refine a {job['status']} job")
    current = job.get("spec_json") or {}
    rough = current.get("source_text") or job["task"]
    # Refinement needs the user's capture, not repository contents. Keeping
    # AGENTS/README and absolute paths out of this cloud call avoids unnecessary
    # project-context egress.
    instructions = (instructions or "").strip()[:2000]
    # The project NAME, not its path or contents. Without it the model has no
    # idea which repo the job targets and fills the gap with whatever project it
    # can see — which is how job #21, targeting TFI-banisa, came back as a
    # complete work order for Code-as-a-Chat.
    project_name = Path(job["project"]).name if job.get("project") else ""
    prompt = f"ROUGH TASK:\n{rough}"
    if project_name:
        prompt = (f"TARGET PROJECT: {project_name}\n"
                  f"The worker will run inside that repository. You cannot see its\n"
                  f"files; plan to read them rather than guessing their contents.\n\n"
                  + prompt)
    # Give the refiner just the nearest capability/task title. This is enough to
    # frame the work as a true extension or distinguish it from a duplicate,
    # without sending repository contents or local paths to the cloud model.
    from server.capability_registry import assess
    awareness = assess(job_id, apply=False)
    match = awareness.get("match") or {}
    if match:
        prompt += ("\n\nPROJECT AWARENESS:\n"
                   f"Closest existing item: {match.get('title')} "
                   f"({match.get('status')}, similarity {match.get('score')}).\n"
                   "If this request extends it, state the distinction explicitly. "
                   "Do not recreate behavior that already exists.")
    if instructions:
        prompt += f"\n\nOWNER'S REFINEMENT INSTRUCTIONS:\n{instructions}"
    # 90s was too tight: a full work order is a long structured generation and
    # a timeout here surfaces to the owner as a hard refinement failure.
    raw = await _claude_cli(_SYSTEM, prompt, timeout=REFINE_TIMEOUT, model="sonnet")
    spec = _parse_complete(raw)
    if spec is None:
        raw = await _claude_cli(
            _SYSTEM + "\nYour previous response was incomplete. Include every field.",
            prompt,
            timeout=REFINE_TIMEOUT,
            model="sonnet",
        )
        spec = _parse_complete(raw)
    if spec is None:
        raise RuntimeError("Claude did not return a complete work order")
    spec.source_text = rough
    mark_refined(spec, provider="claude-sonnet")
    prior = (job.get("summary") or "").strip() if job["status"] == "failed" else ""
    summary = "Refined by Claude Sonnet; held for review."
    if prior:
        summary += f" Previous failure: {prior}"
    night_queue_store.update(
        job_id,
        task=spec.as_task(),
        spec_json=spec.model_dump(),
        status="held",
        tag="mine",
        summary=summary,
    )
    return night_queue_store.get(job_id)
