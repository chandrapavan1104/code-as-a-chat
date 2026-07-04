import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Header, Depends, BackgroundTasks
from pydantic import BaseModel
from server import config, fcm, orchestrator
from server.scheduler import scheduler_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    orchestrator.init()
    task = asyncio.create_task(scheduler_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Code-as-a-Chat Orchestrator", version="0.2.0", lifespan=lifespan)


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


def _preview(text: str, limit: int = _PUSH_PREVIEW_CHARS) -> str:
    """Collapse a reply into a single-line notification body."""
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


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
    """Full manifest — the bot (and future Android client) self-configures from this."""
    from server.skills import manifest
    return {"skills": manifest()}


@app.post("/run", dependencies=[Depends(require_token)])
async def run(body: RunRequest, background_tasks: BackgroundTasks):
    try:
        result = await orchestrator.route(
            body.command, body.prompt, session_id=body.session_id
        )
        # Ping the phone once the (possibly long) run finishes, if asked and we
        # have a session to deep-link back into. Runs after the response is sent.
        if body.notify and body.session_id:
            background_tasks.add_task(
                _push_reply, body.session_id, body.command, result
            )
        return {"command": body.command, "result": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
