"""Start substantial public research as durable background work."""

from server import config, research_runner, workspace
from server.skills import register
from server.skills.base import Skill, SkillResult
from server.skills.queue import _attachment_refs


class ResearchSkill(Skill):
    name = "research"
    description = "Run substantial sourced public research in the background"
    agent_doc = (
        "Start substantial public web research that may take minutes. Use this "
        "instead of claude/codex/antigravity for comparisons, provider or product "
        "research, market scans, and requests requiring multiple current sources. "
        "It returns immediately with a durable task id; the report is saved back "
        "to the same conversation. Do not use for one quick fact or code changes. "
        "args: preserve the user's complete research question and constraints."
    )
    passthrough = True

    async def run(self, prompt: str = "", session_id: str | None = None,
                  **kwargs) -> SkillResult:
        if not session_id:
            return SkillResult("failed", "Background research needs a conversation session.")
        source = (kwargs.get("source_prompt") or prompt).strip()
        if not source:
            return SkillResult("failed", "Background research needs a question.")
        refs = _attachment_refs(
            prompt, session_id=session_id, source_prompt=kwargs.get("source_prompt"))
        work, created = research_runner.submit(
            session_id=session_id, request_id=kwargs.get("request_id"),
            prompt=source, project=kwargs.get("project") or workspace.name(),
            engine=config.RESEARCH_ENGINE, timeout_seconds=config.RESEARCH_TIMEOUT,
            attachment_refs=refs,
        )
        if work["status"] == "waiting_for_user":
            return SkillResult(
                "waiting_for_user",
                "I saved this research request, including its attachments, but need "
                "your approval before sending attachment contents to the research provider. "
                f"Task: {work['id']}",
                data={"task_id": work["id"], "status": work["status"],
                      "command": "research"},
            )
        message = ("Research is continuing in the background. I’ll save the sourced "
                   "report in this conversation when it finishes. "
                   f"Task: {work['id']}")
        return SkillResult(
            "deferred", message,
            data={"task_id": work["id"], "status": work["status"],
                  "command": "research", "deduplicated": not created},
        )


register(ResearchSkill())
