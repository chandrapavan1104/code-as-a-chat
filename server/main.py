import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel
from server import config, orchestrator
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


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/skills", dependencies=[Depends(require_token)])
async def list_skills():
    """Full manifest — the bot (and future Android client) self-configures from this."""
    from server.skills import manifest
    return {"skills": manifest()}


@app.post("/run", dependencies=[Depends(require_token)])
async def run(body: RunRequest):
    try:
        result = await orchestrator.route(
            body.command, body.prompt, session_id=body.session_id
        )
        return {"command": body.command, "result": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
