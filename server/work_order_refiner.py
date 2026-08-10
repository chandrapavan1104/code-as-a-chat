"""Turn a rough queue capture into an executable, reviewable work order."""

from __future__ import annotations

from server.db import night_queue_store
from server.skills.shell import _claude_cli, _parse_json_decision
from server.work_orders import WorkOrderSpec, mark_refined


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
  work, require a localhost server reachable through the Mac's Tailscale IP and
  port. For app work, require the APK/build and exact screen interactions.
- Policy must preserve existing changes, forbid destructive/external actions
  without approval, and keep secrets out of output.
- Research jobs may gather public information and source URLs, but MUST NOT
  contact people, send email/messages, submit forms, log in, purchase, or claim
  an outreach action succeeded. Those actions belong in out_of_scope.
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
    prompt = f"ROUGH TASK:\n{rough}"
    if instructions:
        prompt += f"\n\nOWNER'S REFINEMENT INSTRUCTIONS:\n{instructions}"
    raw = await _claude_cli(_SYSTEM, prompt, timeout=90, model="sonnet")
    spec = _parse_complete(raw)
    if spec is None:
        raw = await _claude_cli(
            _SYSTEM + "\nYour previous response was incomplete. Include every field.",
            prompt,
            timeout=90,
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
