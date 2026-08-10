"""Versioned queue work orders.

Night Shift historically stored one free-form ``task`` string.  Keep that field
as the worker-facing compatibility surface, but parse/store a structured spec so
the phone can show intent, safety boundaries, acceptance, and testing separately.
"""

from __future__ import annotations

import re
import time
from typing import Literal

from pydantic import BaseModel, Field


class WorkOrderSpec(BaseModel):
    version: int = 2
    readiness: Literal["draft", "refined"] = "draft"
    work_type: Literal["coding", "research"] = "coding"
    title: str
    outcome: str = ""
    context: str = ""
    plan: list[str] = Field(default_factory=list)
    policy: str = ""
    acceptance: list[str] = Field(default_factory=list)
    test_handoff: str = ""
    out_of_scope: str = ""
    assumptions: list[str] = Field(default_factory=list)
    source_text: str = ""
    refined_at: float | None = None
    refined_by: str | None = None

    @property
    def is_complete(self) -> bool:
        return bool(
            self.title.strip() and self.outcome.strip() and self.plan
            and self.policy.strip() and self.acceptance
            and self.test_handoff.strip()
        )

    def as_task(self) -> str:
        sections = [f"TITLE: {self.title.strip()}"]
        if self.outcome.strip():
            sections.append(f"OUTCOME: {self.outcome.strip()}")
        if self.context.strip():
            sections.append(f"CONTEXT: {self.context.strip()}")
        if self.plan:
            sections.append("APPROVED PLAN:\n" + "\n".join(
                f"{i}. {step.strip()}" for i, step in enumerate(self.plan, 1)
                if step.strip()
            ))
        if self.policy.strip():
            sections.append(f"POLICY: {self.policy.strip()}")
        if self.acceptance:
            sections.append("ACCEPTANCE:\n" + "\n".join(
                f"- {item.strip()}" for item in self.acceptance if item.strip()
            ))
        if self.test_handoff.strip():
            sections.append(f"TEST HANDOFF: {self.test_handoff.strip()}")
        if self.out_of_scope.strip():
            sections.append(f"OUT OF SCOPE: {self.out_of_scope.strip()}")
        if self.assumptions:
            sections.append("ASSUMPTIONS:\n" + "\n".join(
                f"- {item.strip()}" for item in self.assumptions if item.strip()
            ))
        return "\n".join(s for s in sections if s.strip())


_HEADINGS = {
    "TITLE": "title",
    "OUTCOME": "outcome",
    "CONTEXT": "context",
    "APPROVED PLAN": "plan",
    "PLAN": "plan",
    "POLICY": "policy",
    "ACCEPTANCE": "acceptance",
    "ACCEPTANCE CRITERIA": "acceptance",
    "TEST HANDOFF": "test_handoff",
    "OUT OF SCOPE": "out_of_scope",
    "ASSUMPTIONS": "assumptions",
}
_HEADING_RE = re.compile(
    r"^(TITLE|OUTCOME|CONTEXT|APPROVED PLAN|PLAN|POLICY|ACCEPTANCE|"
    r"ACCEPTANCE CRITERIA|TEST HANDOFF|OUT OF SCOPE|ASSUMPTIONS):\s*(.*)$",
    re.IGNORECASE,
)


def from_task(task: str) -> WorkOrderSpec:
    """Best-effort legacy/free-text task → structured spec, without data loss."""
    task = (task or "").strip()
    buckets: dict[str, list[str]] = {}
    current: str | None = None
    for line in task.splitlines():
        match = _HEADING_RE.match(line.strip())
        if match:
            current = _HEADINGS[match.group(1).upper()]
            buckets.setdefault(current, [])
            if match.group(2).strip():
                buckets[current].append(match.group(2).strip())
        elif current is not None:
            buckets[current].append(line.rstrip())

    title = "\n".join(buckets.get("title", [])).strip()
    if not title:
        title = next((line.strip() for line in task.splitlines() if line.strip()), "Untitled task")

    def text(name: str) -> str:
        return "\n".join(buckets.get(name, [])).strip()

    def items(name: str) -> list[str]:
        raw = buckets.get(name, [])
        values = []
        for line in raw:
            cleaned = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line).strip()
            if cleaned:
                values.append(cleaned)
        return values

    # A legacy one-line job remains fully visible as both title and context.
    context = text("context")
    if len(buckets) <= 1 and task != title:
        context = task
    elif not buckets:
        context = task
    spec = WorkOrderSpec(
        title=title,
        outcome=text("outcome"),
        context=context,
        plan=items("plan"),
        policy=text("policy"),
        acceptance=items("acceptance"),
        test_handoff=text("test_handoff"),
        out_of_scope=text("out_of_scope"),
        assumptions=items("assumptions"),
        source_text=task,
    )
    if spec.is_complete:
        spec.readiness = "refined"
    return spec


def migrate_spec(data: dict | None, task: str) -> WorkOrderSpec:
    """Upgrade stored v1/free-text specs and infer readiness conservatively."""
    if not data:
        return from_task(task)
    original_version = int(data.get("version") or 1)
    had_readiness = data.get("readiness") in ("draft", "refined")
    spec = WorkOrderSpec.model_validate(data)
    spec.version = 2
    if not spec.source_text:
        spec.source_text = task
    if not had_readiness or original_version < 2:
        spec.readiness = "refined" if spec.is_complete else "draft"
    elif spec.readiness == "refined" and not spec.is_complete:
        spec.readiness = "draft"
    # Early Qwen refinements predated work_type and were persisted with the new
    # field's coding default. Correct only unmistakable legacy research specs;
    # Claude handles all future classification explicitly.
    if (spec.refined_by == "local-qwen" and spec.work_type == "coding"
            and _looks_like_research(spec)):
        spec.work_type = "research"
    return spec


def _looks_like_research(spec: WorkOrderSpec) -> bool:
    text = " ".join([
        spec.title, spec.outcome, spec.context, *spec.plan, *spec.acceptance,
    ]).lower()
    research_signals = (
        "conduct web search", "web research", "business listings",
        "potential clients", "lead list", "source urls", "social media",
        "linkedin", "market research",
    )
    code_signals = (
        "implement", "repository", "code change", "endpoint", "flutter",
        "database schema", "unit test", "bug fix",
    )
    return (text.startswith("research ") or sum(s in text for s in research_signals) >= 2) \
        and not any(s in text for s in code_signals)


def mark_refined(spec: WorkOrderSpec, provider: str = "agent") -> WorkOrderSpec:
    spec.version = 2
    spec.readiness = "refined"
    spec.refined_at = time.time()
    spec.refined_by = provider
    return spec
