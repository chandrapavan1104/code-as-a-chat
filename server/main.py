import asyncio
import json
import traceback
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Header, Depends, BackgroundTasks, Request
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
from server import config, fcm, orchestrator
from server.db import store as memory
from server.db import (cli_runs_store, deployment_store, errors_store,
                       night_queue_store, notifications_store)
from server.scheduler import scheduler_loop
from server.night_shift import night_shift_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    cli_runs_store.init()
    night_queue_store.init()
    notifications_store.init()
    deployment_store.init()
    orchestrator.init()
    tasks = [
        asyncio.create_task(scheduler_loop()),
        asyncio.create_task(night_shift_loop()),
    ]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="Code-as-a-Chat Orchestrator", version="0.2.0", lifespan=lifespan)


@app.exception_handler(Exception)
async def _capture_unhandled(request: Request, exc: Exception):
    """Record any unhandled server exception (the fix agent's eyes) then return a
    clean 500. HTTPExceptions have their own handler and don't reach here."""
    errors_store.add(
        "server", "exception", f"{type(exc).__name__}: {exc}",
        detail=traceback.format_exc(),
        context={"path": request.url.path, "method": request.method},
    )
    return JSONResponse(status_code=500, content={"detail": "internal error"})


# ── auth gateway ──────────────────────────────────────────────────────────────

async def require_token(x_api_token: str | None = Header(default=None)) -> None:
    """Every mutating / metadata endpoint requires the shared API token.
    /health stays open for liveness checks."""
    if not config.API_TOKEN:
        return  # auth disabled (token unset) — should not happen in practice
    if x_api_token != config.API_TOKEN:
        raise HTTPException(status_code=401, detail="invalid or missing API token")


# ── API v2 — structured JSON for the native app (token-authed) ────────────────
from server.api_v2 import router as api_v2_router  # noqa: E402
app.include_router(api_v2_router, dependencies=[Depends(require_token)])


class RunRequest(BaseModel):
    command: str
    prompt: str = ""
    # Optional conversation key. Clients namespace it, e.g. "tg:<chat_id>".
    session_id: str | None = None
    # When true, push an FCM "reply is ready" notification once the run
    # completes. The app sets this so a request kicked off then backgrounded
    # still pings the user like a chat message. The app suppresses the
    # notification if it's foregrounded on that same session (see chat_reply
    # handling), so it only ever shows when you're *out* of that chat.
    notify: bool = False


# Longest reply preview carried in a completion push (Android collapses more).
_PUSH_PREVIEW_CHARS = 160
_STREAM_HEARTBEAT_SECONDS = 15
# Keep disconnected stream workers alive until they persist the reply and send
# the completion push. The event loop otherwise only retains weak references.
_stream_workers: set[asyncio.Task] = set()


def _preview(text: str, limit: int = _PUSH_PREVIEW_CHARS) -> str:
    """Collapse a reply into a single-line notification body."""
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def _persist_skill_turn(command: str, session_id: str | None,
                        prompt: str, result: str) -> None:
    """Persist a direct skill-chat turn (claude/codex/gemini/etc.) to memory so
    the app can restore it on reopen. The shell path already records its own
    turns via `_remember`, so skip it here to avoid double-writing."""
    if command == "shell" or not session_id or not result or not result.strip():
        return
    try:
        memory.append_turn(session_id, "user", prompt)
        memory.append_turn(session_id, "assistant", result)
    except Exception:
        pass


async def _push_reply(session_id: str, command: str, result: str) -> None:
    """Fire a chat-style completion push for a finished /run. Best-effort:
    no registered devices or no FCM key → silent no-op."""
    if not result or not result.strip():
        return
    if not fcm.available():
        return
    try:
        await fcm.push_all(
            config.AGENT_NAME,
            _preview(result),
            data={"type": "chat_reply", "session_id": session_id, "command": command},
        )
    except Exception:
        # A push failure must never surface as a /run error — the reply itself
        # already went back over HTTP.
        pass


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/skills", dependencies=[Depends(require_token)])
async def list_skills():
    """Full manifest — the bot (and future Android client) self-configures from this.
    Includes enabled/disabled state for each skill (marketplace toggle)."""
    from server.skills import manifest
    from server import prefs
    skills = manifest()
    # Inject enabled state into each skill
    for skill in skills:
        skill["enabled"] = prefs.is_skill_enabled(skill["name"])
    return {"skills": skills}


class SkillToggleRequest(BaseModel):
    enabled: bool


@app.post("/skills/{skill_name}", dependencies=[Depends(require_token)])
async def toggle_skill(skill_name: str, request: SkillToggleRequest):
    """Enable or disable a skill. Returns the updated skill state."""
    from server.skills import registry
    from server import prefs

    # Validate that the skill exists
    if skill_name not in registry:
        raise HTTPException(status_code=404, detail=f"skill '{skill_name}' not found")

    # Set the enabled state
    prefs.set_skill_enabled(skill_name, request.enabled)

    return {
        "name": skill_name,
        "enabled": request.enabled,
        "message": f"Skill '{skill_name}' is now {'enabled' if request.enabled else 'disabled'}"
    }


@app.post("/run", dependencies=[Depends(require_token)])
async def run(body: RunRequest, background_tasks: BackgroundTasks):
    try:
        result = await orchestrator.route(
            body.command, body.prompt, session_id=body.session_id
        )
        _persist_skill_turn(body.command, body.session_id, body.prompt, result)
        # Ping the phone once the (possibly long) run finishes, if asked and we
        # have a session to deep-link back into. Runs after the response is sent.
        if body.notify and body.session_id:
            background_tasks.add_task(
                _push_reply, body.session_id, body.command, result
            )
        # `workspace` lets the app follow project switches made *during* the turn
        # (e.g. the agent used the projects tool) so its header + thread stay synced.
        return {"command": body.command, "result": result,
                "workspace": config.WORKSPACE_DIR.name}
    except Exception as exc:
        errors_store.add("server", "run", f"{type(exc).__name__}: {exc}",
                         detail=traceback.format_exc(),
                         context={"command": body.command})
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/run/stream", dependencies=[Depends(require_token)])
async def run_stream(body: RunRequest):
    """Same as /run, but streams NDJSON progress while the (possibly long) agent
    turn executes, so the phone shows live steps instead of one silent blob.

    Frames (one JSON object per line):
      {"type":"step","label":"Coding with Claude: fix the auth bug"}
      {"type":"final","result":"<full reply>"}
      {"type":"error","message":"<why>"}

    The completion push still fires on `notify` — the FCM ping is the safety net
    if the phone dropped the stream (backgrounded / killed) before `final`.
    """
    queue: asyncio.Queue = asyncio.Queue()

    async def on_event(ev: dict) -> None:
        await queue.put(ev)

    async def worker() -> None:
        try:
            result = await orchestrator.route(
                body.command, body.prompt,
                session_id=body.session_id, on_event=on_event,
            )
            _persist_skill_turn(body.command, body.session_id, body.prompt, result)
            # `workspace` = the active project *after* the turn, so the app can
            # follow a switch the agent made mid-turn (projects tool) and keep its
            # header + conversation thread in sync.
            await queue.put({"type": "final", "result": result,
                             "workspace": config.WORKSPACE_DIR.name})
            if body.notify and body.session_id:
                await _push_reply(body.session_id, body.command, result)
        except Exception as exc:
            errors_store.add("server", "run_stream", f"{type(exc).__name__}: {exc}",
                             detail=traceback.format_exc(),
                             context={"command": body.command})
            await queue.put({"type": "error", "message": str(exc)})
        finally:
            await queue.put(None)   # sentinel → close the stream

    async def frames():
        task = asyncio.create_task(worker())
        _stream_workers.add(task)
        task.add_done_callback(_stream_workers.discard)
        try:
            # Send body bytes immediately. Qwen's first local inference can be
            # silent for longer than the phone/proxy's 15s connection window;
            # waiting for the first heartbeat made a healthy stream lose that
            # race and surface as HttpException/Reconnecting in Gajala.
            yield json.dumps({"type": "step", "label": "Thinking…"}) + "\n"
            while True:
                try:
                    ev = await asyncio.wait_for(
                        queue.get(), timeout=_STREAM_HEARTBEAT_SECONDS
                    )
                except asyncio.TimeoutError:
                    # Long CLI calls may be silent for minutes. Keep bytes moving
                    # so the HTTPS proxy/client does not drop an otherwise healthy
                    # chunked response; clients safely ignore this frame type.
                    yield json.dumps({"type": "heartbeat"}) + "\n"
                    continue
                if ev is None:
                    break
                yield json.dumps(ev) + "\n"
        finally:
            # A backgrounded phone may lose its response stream. The worker must
            # still finish so its reply is persisted and the user receives FCM.
            pass

    return StreamingResponse(frames(), media_type="application/x-ndjson")
