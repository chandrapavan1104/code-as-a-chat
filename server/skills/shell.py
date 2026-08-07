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
import logging
import re
import shutil
import httpx
from server.skills.base import Skill
from server.skills import register, get_skill
from server.db import store as memory
from server import config


MAX_ITERATIONS = 7         # cap on tool calls per user turn
RESULT_TRUNCATE = 2500     # cap each tool result before feeding back to Haiku
HAIKU_TIMEOUT = 60         # seconds per Haiku call

_log = logging.getLogger(__name__)

# Images a tool produced (e.g. /mac screenshot) are marked in its output as
# "[image: /path]". Haiku might drop the marker when it rewrites the reply, so
# we collect markers mechanically and re-attach any missing ones to the final
# reply — the app renders them, Telegram shows the path.
_IMAGE_MARKER_RE = re.compile(r"\[image:\s*[^\]]+\]")


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
- PROJECT HANDOFFS: the current project keeps its durable idea/state in
  AGENTS.md / CLAUDE.md / GEMINI.md. If the user asks whether an idea was passed
  to this project, call "context" with args "show" — NEVER search notes and
  claim it is missing. If they say "build it", "implement the project", or
  "continue the build" while a current directory is supplied, call the pinned
  coding tool for THAT current project. Do not ask which project they mean.
  Preserve the user's scope verbatim in tool args; never add a different project
  name, feature, or speculative alternative.
- BE ACTION-BIASED. The user prefers things getting done over being walked through manual steps.
- After tool results come in, do NOT just paste them verbatim into "reply". Summarize.
- NEVER PARROT A PAST ERROR. An error in <recent_conversation> (e.g. "OAuth
  session expired", "auth failed", a tool that failed last turn) describes ONE
  earlier attempt — it is NOT the current state and NOT the answer to a new
  request. For the NEW USER MESSAGE, always CALL the relevant tool fresh; only
  report a failure if THIS turn's tool call actually fails. Do not refuse or
  repeat a stale error from history.
- AUTH / LOGIN FAILURES: if a coding tool (claude/codex/gemini) returns an error
  about auth, login, OAuth, "not logged in", "session expired", or 401, that CLI
  needs re-authentication. Do NOT keep retrying it and do NOT invent reasons —
  call the "auth" tool ({"action":"call","tool":"auth","args":"<engine>"}) so the
  user can re-login from their phone, and tell them plainly which engine it was.

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
- SHOWING an image back: some tools return an "[image: /path]" marker (e.g. mac
  screenshot). The app renders that image automatically — do NOT paste the file
  path into your reply and do NOT repeat the marker. Just give a short caption
  ("Here's your screen 📸"); the image attaches on its own.

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

def _provider_chain(task: str) -> list[str]:
    """Ordered LLM providers to try for a given task. Tasks in QWEN_TASKS run on
    the local Qwen model first (free/offline) with Claude+OpenAI behind them;
    every other task keeps Qwen as the FINAL fallback so nothing dies when both
    Claude and OpenAI are exhausted. A global SHELL_LLM_PROVIDER (claude|openai|
    qwen) forces that provider to the front."""
    qwen_tasks = {t.strip() for t in getattr(config, "QWEN_TASKS", "").split(",") if t.strip()}
    chain = ["qwen", "claude", "openai"] if task in qwen_tasks else ["claude", "openai", "qwen"]

    forced = getattr(config, "SHELL_LLM_PROVIDER", "auto").lower()
    if forced in ("claude", "openai", "qwen"):
        chain = [forced] + [p for p in chain if p != forced]

    if not config.OPENAI_API_KEY:
        chain = [p for p in chain if p != "openai"]
    return chain


async def _call_llm(provider: str, system_prompt: str, user_message: str,
                    timeout: int, model: str | None,
                    json_mode: bool = False) -> str:
    # `model` is a Claude-specific alias (e.g. a caller's premium diary model);
    # Qwen and OpenAI use their own configured models, so don't forward it.
    if provider == "qwen":
        return await _qwen_chat(system_prompt, user_message, timeout,
                                json_mode=json_mode)
    if provider == "openai":
        return await _openai_chat(system_prompt, user_message, timeout)
    return await _claude_cli(system_prompt, user_message, timeout, model)


async def _haiku(system_prompt: str, user_message: str, timeout: int = HAIKU_TIMEOUT,
                 model: str | None = None, task: str = "shell",
                 validate=None) -> str:
    """One-shot LLM call for the shell brain (and notes/diary/reminders).

    Routes through _provider_chain(task): local Qwen for QWEN_TASKS (default), with
    Claude + OpenAI as fallback; other tasks fall back TO Qwen last. `validate` (if
    given) is called on each provider's output — a provider whose output fails it
    (e.g. Qwen returned unparseable JSON) is skipped and the next provider tried,
    so bad local JSON transparently escalates to Claude. If every provider fails
    validation, the last successful output is still returned so the caller's own
    salvage logic can have a go rather than erroring."""
    chain = _provider_chain(task)
    last_exc: Exception | None = None
    last_out: str | None = None
    for provider in chain:
        try:
            out = await _call_llm(provider, system_prompt, user_message, timeout,
                                  model, json_mode=validate is not None)
        except Exception as exc:
            last_exc = exc
            _log.warning("shell LLM provider %r failed for task %r (%s)", provider, task, exc)
            continue
        if validate is not None and not validate(out):
            last_out = out
            _log.warning("provider %r output failed validation for task %r — trying next",
                         provider, task)
            continue
        return out
    if last_out is not None:
        return last_out
    raise last_exc or RuntimeError("no LLM provider available")


async def _qwen_chat(system_prompt: str, user_message: str,
                     timeout: int = HAIKU_TIMEOUT, model: str | None = None,
                     json_mode: bool = False) -> str:
    """Local brain: a one-shot Ollama chat completion (Qwen). Same text-in/text-out
    contract as the Claude/OpenAI paths. Raises on connection/HTTP error so the
    provider chain falls through to a cloud model."""
    url = config.OLLAMA_URL.rstrip("/") + "/api/chat"
    payload = {
        "model": model or config.QWEN_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "stream": False,
        "options": {"temperature": 0},
        # Keep the model resident so back-to-back turns don't pay a reload.
        "keep_alive": getattr(config, "QWEN_KEEP_ALIVE", "30m"),
    }
    if json_mode:
        payload["format"] = "json"
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(url, json=payload)
    if r.status_code != 200:
        raise RuntimeError(f"qwen (ollama) failed: {r.status_code} {r.text[:200]}")
    data = r.json()
    try:
        from server.db import local_llm_usage_store
        local_llm_usage_store.record(
            response=data, model=payload["model"], cwd=str(config.WORKSPACE_DIR),
            source="shell",
        )
    except Exception:
        pass
    return ((data.get("message") or {}).get("content") or "").strip()


async def _openai_chat(system_prompt: str, user_message: str,
                       timeout: int = HAIKU_TIMEOUT) -> str:
    """Fallback brain: a one-shot OpenAI chat completion. Same text-in/text-out
    contract as the Claude path so every caller works unchanged."""
    if not config.OPENAI_API_KEY:
        raise RuntimeError("no LLM available: Claude failed and OPENAI_API_KEY is unset")
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
            json={
                "model": config.OPENAI_SHELL_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                "temperature": 0,
            },
        )
    if r.status_code != 200:
        raise RuntimeError(f"openai fallback failed: {r.status_code} {r.text[:200]}")
    data = r.json()
    return (data["choices"][0]["message"]["content"] or "").strip()


async def _claude_cli(system_prompt: str, user_message: str, timeout: int = HAIKU_TIMEOUT,
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
        repeated = self._repeat_last_reply(prompt, recent)
        if repeated is not None:
            self._remember(session_id, prompt, repeated)
            return repeated
        context_block = self._format_context(recent)
        # Combined per-turn hints: pinned engine + the narrow wrong-directory check.
        hints = "\n\n".join(h for h in (self._engine_hint(), self._directory_hint()) if h)
        scratchpad: list[dict] = []
        images: list[str] = []   # [image: …] markers gathered from tool outputs

        for iteration in range(MAX_ITERATIONS):
            agent_input = self._build_agent_input(
                context_block, prompt, scratchpad, hints)

            raw = None
            last_exc: Exception | None = None
            for attempt in range(2):   # one retry — the claude CLI occasionally hiccups
                try:
                    # task="shell" → local Qwen first (default), with a validator
                    # that escalates to Claude/OpenAI if the output isn't a usable
                    # decision (so weak local JSON never breaks routing).
                    raw = await _haiku(
                        get_agent_system(), agent_input, timeout=HAIKU_TIMEOUT,
                        task="shell",
                        validate=lambda o: _parse_json_decision(o) is not None
                        or _salvage_reply(o) is not None)
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

            forced = self._project_intent_action(prompt, scratchpad)
            if forced is not None:
                decision = forced

            action = decision.get("action")
            if action in self.DELEGATE_SKILLS:
                decision["tool"] = action
                action = "call"

            if action == "done":
                final = (decision.get("reply") or "").strip() or "[shell] empty reply"
                final = self._attach_images(final, images)
                self._remember(session_id, prompt, final)
                return final

            if action == "call":
                tool_name = (decision.get("tool") or "").strip()
                tool_args = (decision.get("args") or "").strip()

                # A small router may react to a timeout by launching the exact
                # same expensive call again. That never adds information and can
                # silently burn another 10 minutes, so stop mechanically.
                for previous in reversed(scratchpad):
                    if (previous["tool"] == tool_name
                            and previous["args"] == tool_args
                            and re.search(r"\bTimed out after \d+s\b",
                                          previous["result"], re.IGNORECASE)):
                        final = (previous["result"]
                                 + "\n\nThe identical request was not retried "
                                   "automatically. Narrow the task or ask me to "
                                   "retry it explicitly.")
                        self._remember(session_id, prompt, final)
                        return final

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

                # Remember any image a tool produced, before truncation can clip it.
                images.extend(_IMAGE_MARKER_RE.findall(result))

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
    def _project_intent_action(prompt: str, scratchpad: list[dict]) -> dict | None:
        """Deterministic rail for the two short project handoffs small routers
        repeatedly got wrong: inspect passed context, then run the pinned coding
        agent. Narrow matching avoids hijacking normal planning questions."""
        text = " ".join(prompt.lower().strip(" .!?").split())
        build_commands = {
            "build it", "build the project", "implement it",
            "implement the project", "continue the build",
            "continue building", "continue building the project",
            "finish the build", "finish the project",
        }
        handoff = ("idea" in text and ("passed" in text or "context" in text)
                   and ("build it" in text or "implement it" in text))
        if text not in build_commands and not handoff:
            return None

        if handoff and not any(s["tool"] == "context" for s in scratchpad):
            return {"action": "call", "tool": "context", "args": "show"}

        coding_tools = {"claude", "codex", "antigravity"}
        if any(s["tool"] in coding_tools for s in scratchpad):
            return None
        try:
            from server import prefs
            engine = prefs.get_coding_engine()
            tool = prefs.ENGINE_TOOL.get(engine, engine)
        except Exception:
            tool = "claude"
        if tool not in coding_tools:
            tool = "claude"
        args = prompt
        if handoff:
            args += ("\nUse the current project's AGENTS.md context as the "
                     "authoritative idea and implement it.")
        return {"action": "call", "tool": tool, "args": args}

    @staticmethod
    def _repeat_last_reply(prompt: str, recent: list[dict]) -> str | None:
        """Repeat means repeat: don't let a model regenerate and drift away
        from the immediately preceding update. Deliberately excludes "try/run
        again", which requests a new action rather than the same text."""
        text = " ".join(prompt.lower().split())
        patterns = (
            r"^(?:please )?(?:give|send|show|tell)(?: me)? (?:the )?"
            r"(?:same )?(?:update|answer|reply|message) again[.!?]*$",
            r"^(?:please )?repeat (?:that|it|your (?:last )?"
            r"(?:update|answer|reply|message))[.!?]*$",
        )
        if not any(re.fullmatch(pattern, text) for pattern in patterns):
            return None
        for turn in reversed(recent):
            if turn.get("role") == "assistant" and turn.get("content"):
                return str(turn["content"])
        return None

    @staticmethod
    def _attach_images(reply: str, images: list[str]) -> str:
        """Re-attach any [image: …] markers a tool produced that the agent's
        reply dropped, so the app still renders them. Order-preserving, deduped."""
        if not images:
            return reply
        present = set(_IMAGE_MARKER_RE.findall(reply))
        seen: set[str] = set()
        missing: list[str] = []
        for m in images:
            if m not in present and m not in seen:
                seen.add(m)
                missing.append(m)
        if not missing:
            return reply
        return reply.rstrip() + "\n" + "\n".join(missing)

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
    def _engine_hint() -> str:
        """If the user pinned a coding engine, tell the agent to prefer it for
        code/file work (unless they explicitly name a different one). Empty when
        'auto' — then the agent picks per ask, as before."""
        try:
            from server import prefs
            engine = prefs.get_coding_engine()
        except Exception:
            return ""
        if engine == "auto":
            return ""
        if engine == "qwen":
            # Qwen is a local chat/reasoning model with NO file/system access.
            return ("PINNED ENGINE: the user selected 'qwen' — a local model that "
                    "handles chat, questions and reasoning. Use the 'qwen' tool for "
                    "general / conversational asks. It CANNOT touch files, repos or "
                    "run commands — for coding / file / repo / system work use "
                    "'claude' or 'codex' instead.")
        tool = prefs.ENGINE_TOOL.get(engine, engine)
        return (f"PINNED CODING ENGINE: the user has selected '{engine}'. For any "
                f"coding / file / repo work, use the '{tool}' tool — NOT another "
                f"engine — unless the user explicitly names a different one in "
                f"this message.")

    @staticmethod
    def _directory_hint() -> str:
        """Narrow safety net: only speak up when the message clearly targets a
        DIFFERENT known project than the current directory. Deliberately does NOT
        ask on vague asks, general questions, or directory-independent tasks —
        that broad 'does this belong here?' check just nags."""
        try:
            from server.skills.projects import _candidates
            current = config.WORKSPACE_DIR.name
            names = [p.name for p in _candidates()]
        except Exception:
            return ""
        if not names:
            return ""
        hint = (
            f"CURRENT DIRECTORY: {current}\n"
            f"KNOWN PROJECTS: {', '.join(names)}\n"
            "DIRECTORY CHECK (narrow): If — and ONLY if — this message clearly asks for "
            "coding / file / repo work in a DIFFERENT known project than the CURRENT "
            "DIRECTORY (it explicitly names that other project by name), do NOT do the work "
            "here. Instead finish with a short 'done' reply: name the project you think they "
            "mean and ask whether to switch to it first (once they confirm, use the 'projects' "
            "tool to switch, then do the work). Do NOT trigger for general questions, "
            "notes / reminders / mac / system tasks, follow-ups about the current project, or "
            "when no other known project is explicitly named — in all those cases proceed normally."
        )
        # Confirm-to-move: a general/unrelated question while inside a PROJECT
        # belongs in the 'general' home base, not this project's thread. Offer to
        # move it (the app turns the marker into a one-tap action). Skip when
        # already in general, or for project work / notes / reminders / mac.
        if current.lower() != "general":
            hint += (
                "\nGENERAL-QUESTION CHECK: If this message is a general or standalone "
                "question with NOTHING to do with the current project (general knowledge, "
                "brainstorming, a topic unrelated to this codebase) — and it is NOT a "
                "notes/reminder/mac/system task — do NOT answer it here. Finish with a "
                "short 'done' reply offering to move it to the general chat, and end the "
                "reply with the marker [[move:general]] on its own line. If it's about the "
                "current project or is a tool task, answer normally with NO marker."
            )
        return hint

    @staticmethod
    def _build_agent_input(context_block: str, user_prompt: str,
                           scratchpad: list[dict], hints: str = "") -> str:
        parts = []
        if context_block:
            parts.append(context_block)
            parts.append("")
        if hints:
            parts.append(hints)
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
