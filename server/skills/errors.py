"""
errors skill — show recently captured server/app errors.

Backed by errors_store (server exceptions + Gajala crash reports). The fix agent
reads the same store when diagnosing, so "what went wrong?" and "fix it" share one
source of truth.
"""

import time

from server.db import errors_store
from server.skills.base import Skill
from server.skills import register


def _ago(ts: float) -> str:
    d = max(0, int(time.time() - ts))
    if d < 60:
        return f"{d}s ago"
    if d < 3600:
        return f"{d // 60}m ago"
    if d < 86400:
        return f"{d // 3600}h ago"
    return f"{d // 86400}d ago"


def format_errors(errs: list[dict], detail: bool = False) -> str:
    if not errs:
        return "No errors captured 🎉"
    blocks = []
    for e in errs:
        ctx = e.get("context") or {}
        loc = ctx.get("path") or ctx.get("command") or ctx.get("screen") or ""
        head = f"[{e['source']}/{e.get('kind') or '?'}] {_ago(e['ts'])}"
        if loc:
            head += f" · {loc}"
        block = f"{head}\n  {e['message'][:300]}"
        if detail and e.get("detail"):
            tail = "\n".join(e["detail"].strip().splitlines()[-8:])
            block += f"\n  ---\n{tail}"
        blocks.append(block)
    return "\n\n".join(blocks)


class ErrorsSkill(Skill):
    name = "errors"
    description = "Show recently captured server/app errors."
    final_output = True
    agent_doc = ('Show recently captured errors (server tracebacks + Gajala app '
                 'crashes). Use when diagnosing a bug or when the user asks what '
                 'went wrong. args: "" (recent) | "server" | "app" | "full" '
                 '(with tracebacks) | "clear" | a number.')

    async def run(self, prompt: str = "", **kwargs) -> str:
        arg = prompt.strip().lower()
        if arg == "clear":
            return f"Cleared {errors_store.clear()} error(s)."
        detail = arg == "full"
        source = arg if arg in ("server", "app") else None
        n = int(arg) if arg.isdigit() else 15
        return format_errors(errors_store.recent(n, source), detail=detail)


register(ErrorsSkill())
