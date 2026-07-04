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


# ── live progress labels ──────────────────────────────────────────────────────
# Present-progressive line shown in the app while a tool runs. Keeps the phone
# feeling alive during long agent turns instead of one silent final blob.
_STEP_VERBS = {
    "claude": "Coding with Claude",
    "codex": "Coding with Codex",
    "gemini": "Working with Gemini",
    "notes": "Working on notes",
    "reminders": "Working on reminders",
    "projects": "Working on projects",
    "files": "Working with files",
    "mac": "Controlling the Mac",
    "memory": "Recalling context",
    "ports": "Checking ports",
    "sessions": "Reading sessions",
    "usage": "Reading usage",
    "firebase": "Running Firebase",
}


def _step_label(tool: str, args: str) -> str:
    """Human, single-line 'what's happening now' label for a tool call."""
    verb = _STEP_VERBS.get(tool, f"Running {tool}")
    detail = " ".join((args or "").split())
    if detail:
        if len(detail) > 60:
            detail = detail[:59].rstrip() + "…"
        return f"{verb}: {detail}"
    return f"{verb}…"


async def _emit(on_event, event: dict) -> None:
    """Push a progress event to the streaming client, if one is listening.
    Best-effort — a slow/broken consumer must never stall the agent."""
    if on_event is None:
        return
    try:
        await on_event(event)
    except Exception:
        pass


# ── agent system prompt ───────────────────────────────────────────────────────
# The tool catalog is assembled at runtime from each skill's `agent_doc`
# (see _build_tool_catalog). Skills are the single source of truth.

AGENT_SYSTEM_TEMPLATE = """\
You are the Code-as-a-Chat shell agent, running on a Mac controlled from a phone via Telegram.

You may receive on each iteration:
- An optional <recent_conversation> block — use ONLY for resolving pronouns and follow-ups.
  The actual instruction is in the "NEW USER MESSAGE" section.
- An optional <scratchpad> block — tool calls and results from EARLIER iterations of THIS turn.

Output ONLY a single JSON object — no prose, no markdown fences, no commentary.
CRITICAL: the JSON must be valid on one line — escape every newline inside string
values as \\n (never emit a literal line break inside a JSON string).

Two valid shapes:

(1) Call a tool to gather info or do work:
{"action":"call","tool":"<one of the tools below>","args":"<exact prompt string for that tool>"}
   Optional: add "final":true when the tool's output is ALREADY a complete,
   well-formatted answer (status readouts, lists, confirmations) and you don't
   need to add your voice or call another tool. This returns the tool's output
   directly and saves a step. Only use it when no persona-add is needed — for
   anything conversational, or when chaining more tools, omit it.

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

ATTACHMENTS & MEDIA:
- The user message may include markers like:
    [User sent an image, saved at: /path/img.jpg]
    [User sent a file 'report.pdf' (application/pdf), saved at: /path]
    [User sent a sticker — emoji 😂 ...]
    [Replying to earlier message: "<quote>"]
- You cannot see images or files yourself, but the "claude" tool CAN — it reads
  images (screenshots, photos, error dialogs), PDFs, and any text/code file.
  For any question about an attachment, delegate:
    {"action":"call","tool":"claude","args":"Read the image at /path/img.jpg and <user's question>. Be concise."}
- NEVER say you can't see an image — claude is your eyes. Use it.
- Screenshots of errors are common: have claude read the image, identify the
  error, and suggest the fix — chain more tools if the fix needs project work.
- Stickers: react in persona based on the emoji; inspect the image only if needed.
- Only audio/video can't be processed yet — be honest about those.

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
{PERSONA}"""


PERSONA_TELUGU_BESTIE = """
PERSONA — WHO YOU ARE (applies ONLY to the "reply" text, never to tool args):
- Nee peru {NAME} — the user's Telugu best friend. Running gag: you're
  "{NAME}... from Washington DC" (drop this intro rarely, for comedy timing).
- Speak Tinglish: Telugu in English script, mixed naturally with English tech
  words. Telugu Twitter / meme-page style. Examples of your vibe:
    "Em sangathi mava, deploy aipoindi 🔥"
    "Orey CPU 90% undi ra, edo process pichhi ekkinchindi"
    "Notes anni capture chesa anna, lite teesko"
    "Scene aypoindi ra... build failed 💀 stack trace chudu"
- Vocabulary you naturally use: mava, ra/raa, orey, anna, em sangathi, keka,
  kummesav, thoppu, vere level, industry hit, scene aypoindi, lite teesko,
  full kick, asalu, pichhi, dorikipoyav, vadiley.
- HYPE the user when something works: "Kummesav mava 🔥🔥", "Industry hit ra idi".
- Light, loving roast when they procrastinate, repeat a question, or have
  10 open todos: friendly teasing, never mean.
- Mild gaalis okay occasionally (orey erri fellow, sachinoda, dobbey) — mirror
  the user's energy. They cuss, you can cuss a bit. Never harsh slurs.
- Emojis welcome: 🔥😂💀🙏🥲. Text-meme references welcome.
- READ THE ROOM: if the user is stressed, debugging something serious, or asks
  for something urgent — drop the comedy, be the supportive friend who fixes
  things fast. Persona is seasoning, not the meal.
- ACCURACY IS SACRED: numbers, file paths, session IDs, URLs, command outputs,
  error messages stay EXACT and unchanged. Code stays in ``` fences in English.
  Never let comedy corrupt data.
- Tool calls (action=call) stay plain precise English — persona never touches them.
"""


def _build_tool_catalog() -> str:
    """Assemble the agent's tool catalog from each skill's manifest agent_doc.
    Built from the live registry, so dropping a new skill file makes it
    agent-visible automatically — no edit here."""
    from server.skills import registry
    lines = []
    for name in sorted(registry):
        sk = registry[name]
        if name == "shell" or not getattr(sk, "expose_to_agent", True):
            continue
        doc = (getattr(sk, "agent_doc", "") or sk.description).strip()
        lines.append(f'- "{name}": {doc}')
    return "\n".join(lines)


def _build_agent_system() -> str:
    persona = ""
    if config.AGENT_PERSONA != "professional":
        persona = PERSONA_TELUGU_BESTIE.replace("{NAME}", config.AGENT_NAME)
    return (AGENT_SYSTEM_TEMPLATE
            .replace("{TOOL_DESCRIPTIONS}", _build_tool_catalog())
            .replace("{PERSONA}", persona))


# Built lazily on first message (after discovery has registered all skills),
# then cached for the process lifetime.
_AGENT_SYSTEM_CACHE: str | None = None


def get_agent_system() -> str:
    global _AGENT_SYSTEM_CACHE
    if _AGENT_SYSTEM_CACHE is None:
        _AGENT_SYSTEM_CACHE = _build_agent_system()
    return _AGENT_SYSTEM_CACHE


# ── Haiku subprocess helper ───────────────────────────────────────────────────

async def _haiku(system_prompt: str, user_message: str, timeout: int = HAIKU_TIMEOUT,
                 model: str | None = None) -> str:
    """One-shot LLM call via the Claude CLI. Tools disabled — pure text in/out.
    Defaults to the fast shell model; pass `model=` for quality-sensitive skills."""
    if shutil.which("claude") is None:
        raise RuntimeError("claude CLI not installed")

    cmd = [
        "claude",
        "-p", user_message,
        "--model", model or config.SHELL_MODEL,
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


def _repair_json(snippet: str) -> dict | None:
    """Fix the most common LLM JSON sin: literal newlines/tabs inside strings
    (invalid JSON — they must be \\n / \\t escaped). Walks the text tracking
    string state and escapes control chars found inside strings."""
    out: list[str] = []
    in_str = False
    escape = False
    for ch in snippet:
        if escape:
            out.append(ch)
            escape = False
            continue
        if ch == "\\":
            out.append(ch)
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            out.append(ch)
            continue
        if in_str and ch == "\n":
            out.append("\\n")
            continue
        if in_str and ch == "\t":
            out.append("\\t")
            continue
        if in_str and ch == "\r":
            continue
        out.append(ch)
    try:
        return json.loads("".join(out))
    except json.JSONDecodeError:
        return None


def _parse_json_decision(raw: str) -> dict | None:
    """Locate and parse the first JSON object in the LLM output.
    Tries strict parse → brace-matched parse → control-char repair."""
    if not raw:
        return None
    brace = raw.find("{")
    if brace < 0:
        return None
    candidate = raw[brace:]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    # Brace-matched substring (handles trailing prose after the JSON)
    depth = 0
    in_str = False
    escape = False
    for i, ch in enumerate(candidate):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                sub = candidate[:i + 1]
                try:
                    return json.loads(sub)
                except json.JSONDecodeError:
                    return _repair_json(sub)
    # Unbalanced braces (e.g. model got cut off) — repair whatever we have
    return _repair_json(candidate)


def _salvage_reply(raw: str) -> str | None:
    """Last resort: model meant to reply but the JSON is unfixable. Pull the
    reply text out by pattern so the user never sees a parse error."""
    import re
    m = re.search(r'"reply"\s*:\s*"(.*)', raw, flags=re.DOTALL)
    if not m:
        return None
    text = m.group(1)
    text = re.sub(r'"\s*}\s*$', "", text.strip())   # trailing "}
    text = text.rstrip('"')                          # dangling quote
    text = (text.replace("\\n", "\n")
                .replace('\\"', '"')
                .replace("\\t", "\t"))
    return text.strip() or None


# ── agent loop ────────────────────────────────────────────────────────────────

class ShellSkill(Skill):
    name = "shell"
    description = ("LLM agent — plans, chains multiple tool calls, "
                   "mobile-formatted replies")

    # Both sets derive from skill manifests at call time — a new skill that
    # sets expose_to_agent / passthrough is picked up with no edit here.
    @property
    def DELEGATE_SKILLS(self) -> set[str]:
        from server.skills import registry
        return {
            n for n, s in registry.items()
            if n != "shell" and getattr(s, "expose_to_agent", True)
        }

    @property
    def PASSTHROUGH_SKILLS(self) -> set[str]:
        from server.skills import registry
        return {n for n, s in registry.items() if getattr(s, "passthrough", False)}

    async def run(self, prompt: str = "", session_id: str | None = None, **kwargs) -> str:
        # Streaming clients pass on_event to receive live progress; None = the
        # classic single-response path (Telegram bot, plain /run).
        on_event = kwargs.get("on_event")
        prompt = prompt.strip()
        if not prompt:
            return (f"Em sangathi mava! {config.AGENT_NAME} ikkada 🔥\n"
                    "(from Washington DC, obviously)\n\n"
                    "Cheppu — code, deploy, notes, reminders, Mac control... "
                    "anything. Multi-step requests kuda kummestha.\n\n"
                    "Explicit: /claude /codex /gemini /notes /projects /sessions "
                    "/firebase /usage /ports /memory /forget /status /files")

        recent = memory.get_recent(session_id, n=config.MEMORY_TURNS) if session_id else []
        context_block = self._format_context(recent)
        scratchpad: list[dict] = []

        for iteration in range(MAX_ITERATIONS):
            agent_input = self._build_agent_input(context_block, prompt, scratchpad)

            raw = None
            last_exc: Exception | None = None
            for attempt in range(2):   # one retry — the claude CLI occasionally hiccups
                try:
                    raw = await _haiku(get_agent_system(), agent_input, timeout=HAIKU_TIMEOUT)
                    break
                except Exception as exc:
                    last_exc = exc
                    if attempt == 0:
                        await asyncio.sleep(1.0)
            if raw is None:
                # LLM unreachable even after a retry. If earlier steps already ran,
                # surface what we did instead of dropping it all with an opaque error.
                if scratchpad:
                    final = self._partial_summary(
                        scratchpad, note=f"(couldn't compose a final reply: {last_exc})")
                else:
                    final = f"[shell] agent LLM error after {iteration} step(s): {last_exc}"
                self._remember(session_id, prompt, final)
                return final

            decision = _parse_json_decision(raw)
            if not decision:
                # Unfixable JSON — salvage the reply text rather than erroring.
                final = (_salvage_reply(raw)
                         or raw.strip()
                         or "[shell] no response")
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

                await _emit(on_event, {"type": "step",
                                       "label": _step_label(tool_name, tool_args)})
                try:
                    result = await skill.run(tool_args, session_id=session_id)
                except Exception as exc:
                    result = f"ERROR running {tool_name}: {exc}"

                if not isinstance(result, str):
                    result = str(result)

                # Different-persona skills (e.g. diary/Anna) reply to the user
                # directly — their voice must never be rewritten by this agent.
                if tool_name in self.PASSTHROUGH_SKILLS and not result.startswith("ERROR"):
                    self._remember(session_id, prompt, result)
                    return result

                # LLM-call saver: when the agent flags "final" AND the skill's
                # output is presentation-ready (final_output), return it directly
                # instead of spending a second LLM call to reformat. The agent
                # only sets this when no persona-add is needed (status, lists,
                # confirmations).
                if (decision.get("final")
                        and getattr(skill, "final_output", False)
                        and not result.startswith("ERROR")):
                    self._remember(session_id, prompt, result)
                    return result

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
            final = self._partial_summary(
                scratchpad, note=f"(stopped at the step limit of {MAX_ITERATIONS})")
        else:
            final = (f"Hit step limit ({MAX_ITERATIONS}) with no completed steps. "
                     "Try a more specific request.")
        self._remember(session_id, prompt, final)
        return final

    @staticmethod
    def _partial_summary(scratchpad: list[dict], note: str = "") -> str:
        """One-line-per-step recap of what actually ran — used when the agent
        can't produce a clean final reply (LLM error or step limit)."""
        lines = ["Here's what I got done:"]
        for i, step in enumerate(scratchpad, 1):
            first = (step["result"].splitlines() or [""])[0]
            lines.append(f"  {i}. {step['tool']}({step['args'][:40]}): {first[:80]}")
        if note:
            lines.append(note)
        return "\n".join(lines)

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
