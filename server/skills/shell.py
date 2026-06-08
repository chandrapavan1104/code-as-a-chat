"""
The shell skill — an agent loop powered by Haiku.

Each user turn becomes up to MAX_ITERATIONS rounds:
  1. Haiku reads: recent memory + user msg + scratchpad of tool calls so far
  2. Haiku outputs either {"action":"call", ...} or {"action":"done", ...}
  3. If "call": run the skill, append result to scratchpad, loop
  4. If "done": return reply to user, store turn in memory

This replaces the old classify-then-delegate-once pattern. Multi-step requests
like "delete notes 1-6 and create new ones for projects A,B,C" now work
because Haiku can plan and execute a sequence.
"""

import asyncio
import json
import shutil
from server.skills.base import Skill
from server.skills import register, get_skill
from server.db import store as memory
from server import config


MAX_ITERATIONS = 7         # cap on tool calls per user turn
RESULT_TRUNCATE = 2500     # cap each tool result before feeding back to Haiku
HAIKU_TIMEOUT = 60         # seconds per Haiku call


# ── tool catalog ──────────────────────────────────────────────────────────────
# Kept verbatim from the old classifier — these descriptions are well-tuned.

TOOL_DESCRIPTIONS = """\
- "claude": Heavy coding agent. Use for: code reading/writing, multi-file refactors, running tests, git ops, debugging, anything that needs to touch the filesystem.
- "codex": OpenAI coding agent. Same capability as claude. Pick when user explicitly says "codex" or "openai".
- "antigravity": Google Gemini agent. Pick when user explicitly says "gemini" or "google".
- "sysmon": Returns CPU/RAM/disk/processes/battery. args="" (empty).
- "filemanager": Lists a directory or reads a file. args=path (e.g. "~/Desktop").
- "notes": Personal whiteboard / scratchpad. Capture bugs, ideas, todos, features, questions.
   Each note auto-tags itself to a known project when the user names one. Use this whenever the
   user is BRAIN-DUMPING, REPORTING an issue, JOTTING an idea, ADDING a todo, OR asking to
   BROWSE past notes. When in doubt between answering directly and capturing, CAPTURE — the user
   wants nothing lost.
    args:
      "capture <user's full text>"   store a new note (LLM auto-extracts kind/project/tags).
                                      If the text covers MULTIPLE projects/topics, the notes skill
                                      will split into multiple notes automatically. So you can pass
                                      a long brain-dump as ONE capture call.
      ""                              list recent open notes
      "<kind>"                        filter (bugs, features, ideas, todos, questions)
      "<project-name>"                filter to a project
      "show <id>"                     view full
      "done <id>" / "drop <id>"       change status
      "search <query>"                full-text search
      "stats"                         counts per project × kind
      "wipe all" / "wipe 1-6"         bulk delete (only when user explicitly asks)
- "usage": Local LLM usage report (Codex/Claude/Gemini/Antigravity). Reads on-disk session files.
   Shows real rate-limit % for Codex; computed % vs daily token budget for Claude and Gemini.
    args: "" | "<provider>" | "today" | "days <n>" | "threads [provider]"
- "firebase": Deploy/preview the active workspace to Firebase. User must have /projects switched
   into a Firebase project (one with firebase.json).
    args: "" (status) | "list" | "use <id>" | "deploy" | "deploy hosting" | "preview [name]"
- "ports": Listening TCP ports and who owns them. Can also kill processes.
    args: "" (list) | "mine" | "show <port>" | "kill <pid>" | "kill :<port>"
- "projects": Switch the active project directory (workspace) for all subsequent skills.
    args: "" (list) | "current" | "switch <name-or-path>"
- "sessions": Browse past chats from claude/codex/gemini.
    args: "" (list recent) | "claude"|"codex"|"gemini" (filter)
        | "show <id-prefix>" | "count"
- "context": Universal project context shared by all agents — the AGENTS.md /
   CLAUDE.md / GEMINI.md files in the active workspace. Use this when the user
   asks about "project context", wants to record what a project is/does, switch
   agents while keeping continuity, or log a change to the shared context.
    args: "" (show) | "status" | "init" (template) | "refresh" (Claude analyzes
          + writes it) | "sync" (converge the 3 files) | "add <changelog entry>"
- "reminders": Time-based Telegram alerts. The user gets a push when due.
    args: "<when> <what>" to create (e.g. "in 2 hours check the deploy",
          "tomorrow 9am standup"), "list" (upcoming), "delete <id>".
    Examples:
      "remind me tomorrow at 5pm to deploy"   → "tomorrow at 5pm deploy"
      "what reminders do I have"              → "list"
- "mac": Remote-control the physical Mac. Flex / utility.
    args: "lock" | "sleep" | "say <text>" | "notify <text>" | "screenshot"
        | "photo" (webcam) | "open <url>" | "bluetooth on|off|toggle".
    Examples:
      "lock my mac"               → "lock"
      "make my mac say hello"     → "say hello"
      "take a screenshot"         → "screenshot"
      "snap a webcam photo"       → "photo"
      "show a notification saying done" → "notify done"
      "turn off bluetooth"        → "bluetooth off"
      "is bluetooth on"           → "bluetooth"
"""


# ── agent system prompt ───────────────────────────────────────────────────────

AGENT_SYSTEM_TEMPLATE = """\
You are the Code-as-a-Chat shell agent, running on a Mac controlled from a phone via Telegram.

You may receive on each iteration:
- An optional <recent_conversation> block — use ONLY for resolving pronouns and follow-ups.
  The actual instruction is in the "NEW USER MESSAGE" section.
- An optional <scratchpad> block — tool calls and results from EARLIER iterations of THIS turn.

Output ONLY a single JSON object — no prose, no markdown fences, no commentary.

Two valid shapes:

(1) Call a tool to gather info or do work:
{"action":"call","tool":"<one of the tools below>","args":"<exact prompt string for that tool>"}

(2) Finish and reply to the user:
{"action":"done","reply":"<your final user-facing reply, plain-text, mobile-friendly>"}

AVAILABLE TOOLS:
{TOOL_DESCRIPTIONS}

DECISION RULES:
- YOU HAVE ALL THE TOOLS LISTED ABOVE — they are real, connected, and running on
  this Mac. NEVER claim you "don't have access". NEVER tell the user to do
  something manually (open a menu, click an icon, use Terminal, check a website)
  when a tool below can do it. If a request maps to a tool, CALL IT.
- For greetings, general-knowledge, "what can you do", "what commands exist":
  go STRAIGHT to "done" with no tool calls.
- For requests that need real action (read state, modify data, run a command):
  call the right tool. After seeing its result, decide if you need another tool
  or you're ready to finish.
- For COMPOUND requests like "delete X and create Y", "wipe these and add those",
  "switch project and then list its files": CALL TOOLS IN SEQUENCE. Don't just
  describe steps — execute them.
  Example: user says "wipe my notes and create ones for projects A, B, C":
    iter 1 → {"action":"call","tool":"notes","args":"wipe all"}
    iter 2 → {"action":"call","tool":"notes","args":"capture <user's project breakdown>"}
    iter 3 → {"action":"done","reply":"Wiped N notes. Captured M new ones for A, B, C."}
- BE ACTION-BIASED. The user prefers things getting done over being walked through manual steps.
- After tool results come in, do NOT just paste them verbatim into "reply". Summarize.

CONSTRAINTS:
- Max 7 tool calls per turn. If you hit it, stop with "done" and explain what got finished.
- Never invent tool names — only use the ones listed above.
- "args" must be a single string. Multi-line strings are fine.

FORMATTING for the "reply" field (when "action" is "done"):
- Plain text only. NO markdown bold (**), italic (_), or headings (#) — Telegram won't render them.
- Short lines (under ~50 chars where natural).
- Bullets "• " or "- ".
- UPPERCASE labels with colon ("STATUS:", "DONE:") instead of bold.
- Triple-backtick fences for code are OK — Telegram does render those.
- Lead with the bottom-line answer on line 1.
- Strip filler: "Let me...", "I'll now...", "Here is...".
- Max ~200 words unless a tool's verbose output is truly needed.
"""

AGENT_SYSTEM = AGENT_SYSTEM_TEMPLATE.replace("{TOOL_DESCRIPTIONS}", TOOL_DESCRIPTIONS)


# ── Haiku subprocess helper ───────────────────────────────────────────────────

async def _haiku(system_prompt: str, user_message: str, timeout: int = HAIKU_TIMEOUT) -> str:
    """One-shot Haiku call via the Claude CLI. Tools disabled — pure text in/out."""
    if shutil.which("claude") is None:
        raise RuntimeError("claude CLI not installed")

    cmd = [
        "claude",
        "-p", user_message,
        "--model", config.SHELL_MODEL,
        "--system-prompt", system_prompt,
        "--output-format", "json",
        "--permission-mode", "bypassPermissions",
        "--tools", "",
        "--strict-mcp-config",   # ignore the user's global MCP servers (Canva, etc.)
        "--no-session-persistence",
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        raise

    if proc.returncode != 0:
        raise RuntimeError(stderr_b.decode(errors="replace").strip() or "haiku call failed")

    try:
        data = json.loads(stdout_b.decode(errors="replace"))
    except json.JSONDecodeError:
        return stdout_b.decode(errors="replace").strip()

    return (data.get("result") or "").strip()


def _parse_json_decision(raw: str) -> dict | None:
    """Locate and parse the first JSON object in the LLM output."""
    if not raw:
        return None
    brace = raw.find("{")
    if brace < 0:
        return None
    try:
        return json.loads(raw[brace:])
    except json.JSONDecodeError:
        depth = 0
        for i, ch in enumerate(raw[brace:], start=brace):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[brace:i + 1])
                    except json.JSONDecodeError:
                        return None
        return None


# ── agent loop ────────────────────────────────────────────────────────────────

class ShellSkill(Skill):
    name = "shell"
    description = ("LLM agent — plans, chains multiple tool calls, "
                   "mobile-formatted replies")

    DELEGATE_SKILLS = {
        "claude", "codex", "antigravity",
        "sysmon", "filemanager",
        "sessions", "projects", "ports", "firebase", "usage",
        "notes", "context", "reminders", "mac",
    }

    async def run(self, prompt: str = "", session_id: str | None = None, **kwargs) -> str:
        prompt = prompt.strip()
        if not prompt:
            return ("Hi — I'm the shell agent.\n"
                    "Send anything: I'll answer, route to a skill, or chain "
                    "multiple skills if your request has multiple steps.\n"
                    "Explicit: /claude /codex /gemini /notes /projects /sessions "
                    "/firebase /usage /ports /memory /forget /status /files")

        recent = memory.get_recent(session_id, n=config.MEMORY_TURNS) if session_id else []
        context_block = self._format_context(recent)
        scratchpad: list[dict] = []

        for iteration in range(MAX_ITERATIONS):
            agent_input = self._build_agent_input(context_block, prompt, scratchpad)

            try:
                raw = await _haiku(AGENT_SYSTEM, agent_input, timeout=HAIKU_TIMEOUT)
            except Exception as exc:
                final = f"[shell] agent LLM error after {iteration} step(s): {exc}"
                self._remember(session_id, prompt, final)
                return final

            decision = _parse_json_decision(raw)
            if not decision:
                final = f"[shell] couldn't parse agent JSON:\n{raw[:400]}"
                self._remember(session_id, prompt, final)
                return final

            action = decision.get("action")

            if action == "done":
                final = (decision.get("reply") or "").strip() or "[shell] empty reply"
                self._remember(session_id, prompt, final)
                return final

            if action == "call":
                tool_name = (decision.get("tool") or "").strip()
                tool_args = (decision.get("args") or "").strip()

                if tool_name not in self.DELEGATE_SKILLS:
                    available = ", ".join(sorted(self.DELEGATE_SKILLS))
                    scratchpad.append({
                        "tool": tool_name,
                        "args": tool_args,
                        "result": f"ERROR: unknown tool. Available: {available}",
                    })
                    continue

                skill = get_skill(tool_name)
                if skill is None:
                    scratchpad.append({
                        "tool": tool_name, "args": tool_args,
                        "result": f"ERROR: skill {tool_name!r} not registered",
                    })
                    continue

                try:
                    result = await skill.run(tool_args, session_id=session_id)
                except Exception as exc:
                    result = f"ERROR running {tool_name}: {exc}"

                if not isinstance(result, str):
                    result = str(result)
                if len(result) > RESULT_TRUNCATE:
                    result = (result[:RESULT_TRUNCATE]
                              + f"\n… (truncated, full was {len(result)} chars)")

                scratchpad.append({
                    "tool": tool_name,
                    "args": tool_args,
                    "result": result,
                })
                continue

            final = f"[shell] unknown agent action: {action!r}"
            self._remember(session_id, prompt, final)
            return final

        # Hit MAX_ITERATIONS without "done"
        if scratchpad:
            lines = [f"Hit step limit ({MAX_ITERATIONS} tool calls). Summary:"]
            for i, step in enumerate(scratchpad, 1):
                first = (step["result"].splitlines() or [""])[0]
                lines.append(f"  {i}. {step['tool']}({step['args'][:40]}): {first[:80]}")
            final = "\n".join(lines)
        else:
            final = (f"Hit step limit ({MAX_ITERATIONS}) with no completed steps. "
                     "Try a more specific request.")
        self._remember(session_id, prompt, final)
        return final

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _build_agent_input(context_block: str, user_prompt: str,
                           scratchpad: list[dict]) -> str:
        parts = []
        if context_block:
            parts.append(context_block)
            parts.append("")
        parts.append(f"NEW USER MESSAGE:\n{user_prompt}")

        if scratchpad:
            parts.append("\n<scratchpad>")
            for i, step in enumerate(scratchpad, 1):
                parts.append(f"\nStep {i}: tool={step['tool']}  args={step['args'][:200]}")
                parts.append("result:")
                for line in step["result"].splitlines():
                    parts.append(f"  {line}")
            parts.append("</scratchpad>")
            parts.append("\nDecide your next action: call another tool, or finish with 'done'.")

        return "\n".join(parts)

    @staticmethod
    def _format_context(recent: list[dict]) -> str:
        if not recent:
            return ""
        lines = ["<recent_conversation>"]
        for t in recent:
            role = "USER" if t["role"] == "user" else "ASSISTANT"
            content = t["content"]
            if len(content) > 600:
                content = content[:597] + "…"
            lines.append(f"{role}: {content}")
        lines.append("</recent_conversation>")
        return "\n".join(lines)

    @staticmethod
    def _remember(session_id: str | None, user_msg: str, assistant_msg: str) -> None:
        if not session_id or not assistant_msg:
            return
        memory.append_turn(session_id, "user", user_msg)
        memory.append_turn(session_id, "assistant", assistant_msg)


register(ShellSkill())
