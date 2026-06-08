import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from server import orchestrator
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


app = FastAPI(title="Code-as-a-Chat Orchestrator", version="0.1.0", lifespan=lifespan)


class RunRequest(BaseModel):
    command: str
    prompt: str = ""
    # Optional conversation key. When supplied, the shell skill uses it to
    # store and retrieve recent turns. Clients should namespace, e.g.
    # "tg:<telegram_chat_id>".
    session_id: str | None = None


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/skills")
async def list_skills():
    from server.skills import registry
    return {"skills": {name: skill.description for name, skill in registry.items()}}


@app.post("/run")
async def run(body: RunRequest):
    try:
        result = await orchestrator.route(
            body.command, body.prompt, session_id=body.session_id
        )
        return {"command": body.command, "result": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
