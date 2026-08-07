"""
qwen skill — chat directly with the local Ollama Qwen2.5-7B model.

A real back-and-forth: the skill feeds recent conversation history to Ollama's
chat API, so Qwen follows context across turns instead of answering each message
in isolation. Runs fully local (no quota, no cloud) — needs Ollama at
localhost:11434 with qwen2.5:7b pulled.

Open a "qwen" chat tab in Gajala and just talk to it; the app persists each turn
under the tab's session, and this skill replays them into the model.
"""

import httpx
from server import config, prefs
from server.db import local_llm_usage_store
from server.db import store as memory
from server.skills.base import Skill
from server.skills import register

_SYSTEM = ("You are Qwen2.5, a helpful local assistant running on the user's own "
           "Mac. Be concise and conversational — replies are read on a phone.")


class QwenSkill(Skill):
    name = "qwen"
    description = "Chat with the local Qwen2.5-7B model (Ollama, no quota)"
    agent_doc = ("Local Qwen2.5-7B chat model — runs on this Mac via Ollama, no "
                 "cloud/quota. Use when the user explicitly says 'qwen' or wants a "
                 "local/offline model. Plain conversation only (no file/system "
                 "access). args: the message to send.")
    expose_to_agent = True
    final_output = True   # its reply is the user-facing answer; no reformat needed

    async def run(self, prompt: str = "", session_id: str | None = None,
                  **kwargs) -> str:
        prompt = prompt.strip()
        if not prompt:
            return "Usage: qwen <message> — or open a Qwen chat tab and just talk."

        # Replay recent turns so Qwen follows the conversation. The app persists
        # each skill-tab turn under this session_id (main._persist_skill_turn).
        messages = [{"role": "system", "content": _SYSTEM}]
        if session_id:
            for turn in memory.get_recent(session_id, n=config.MEMORY_TURNS):
                role = "assistant" if turn["role"] == "assistant" else "user"
                messages.append({"role": role, "content": turn["content"]})
        messages.append({"role": "user", "content": prompt})

        model = prefs.get_coding_model("qwen") or config.QWEN_MODEL
        url = config.OLLAMA_URL.rstrip("/") + "/api/chat"
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                r = await client.post(
                    url,
                    json={"model": model, "messages": messages, "stream": False,
                          "keep_alive": getattr(config, "QWEN_KEEP_ALIVE", "30m")},
                )
                r.raise_for_status()
                data = r.json()
                source = ("gajala" if session_id and session_id.startswith("app:")
                          else "telegram" if session_id and session_id.startswith("tg:")
                          else "server")
                local_llm_usage_store.record(
                    response=data, model=model, cwd=str(config.WORKSPACE_DIR), source=source,
                    session_id=session_id)
                reply = (data.get("message") or {}).get("content", "").strip()
                return reply or "[qwen] (empty response)"
        except httpx.ConnectError:
            return ("[qwen] Can't reach Ollama at localhost:11434 — start it on the "
                    "Mac with `ollama serve` (and `ollama pull qwen2.5:7b`).")
        except httpx.TimeoutException:
            return "[qwen] Timed out after 120s — the local model may be loading; try again."
        except Exception as e:
            return f"[qwen] error: {type(e).__name__}: {e}"


register(QwenSkill())
