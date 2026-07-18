"""
API v2 — structured JSON endpoints for the native (Flutter) app.

The conversational path (/run) returns Gajala's chat-formatted text. These
endpoints return clean JSON so the app can render native cards, gauges, and
lists. Both layers read the same data stores.

Mounted under /api in main.py with the shared token auth applied there.
"""

import time
import json as _json
import shutil
import subprocess
import uuid
from pathlib import Path
import psutil
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from server import config as cfg
from server import fcm
from server.db import notes_store, diary_store, reminders_store
from server.db import devices_store
from server.db import store as memory
from server.media import ensure_uploads_dir, is_served_path
from server.skills.projects import _candidates as _project_candidates, _switch_view as _switch_workspace

router = APIRouter(prefix="/api", tags=["app"])

# Cap an inbound image upload. Phone screenshots/photos are a few MB; 25 MB is
# generous headroom without inviting abuse.
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".bmp"}


# ── chat history (so the app's chat persists, synced with Gajala's memory) ─────

@router.get("/chat")
def chat_history(session_id: str, limit: int = 50):
    return {"turns": memory.get_recent(session_id, n=limit)}


# ── system ────────────────────────────────────────────────────────────────────

@router.get("/system")
def system_stats():
    cpu = psutil.cpu_percent(interval=0.3)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    bat = psutil.sensors_battery()

    procs = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
        try:
            procs.append(p.info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    top = sorted(procs, key=lambda x: x.get("cpu_percent") or 0, reverse=True)[:5]

    return {
        "cpu_percent": cpu,
        "ram": {"percent": mem.percent, "used_gb": round(mem.used / 1024**3, 1),
                "total_gb": round(mem.total / 1024**3, 1)},
        "disk": {"percent": disk.percent, "used_gb": round(disk.used / 1024**3, 1),
                 "total_gb": round(disk.total / 1024**3, 1)},
        "battery": ({"percent": round(bat.percent), "charging": bat.power_plugged}
                    if bat else None),
        "top_processes": [
            {"pid": p["pid"], "name": p["name"],
             "cpu": round(p.get("cpu_percent") or 0, 1),
             "mem": round(p.get("memory_percent") or 0, 1)}
            for p in top
        ],
        "ts": time.time(),
    }


# ── notes ─────────────────────────────────────────────────────────────────────

class NoteIn(BaseModel):
    project: str | None = None
    kind: str = "note"
    title: str
    body: str = ""
    tags: list[str] = []


class NotePatch(BaseModel):
    status: str | None = None   # open | done | dropped
    body: str | None = None


@router.get("/notes")
def list_notes(status: str | None = "open", kind: str | None = None,
               project: str | None = None, limit: int = 100):
    return {"notes": notes_store.list_notes(project=project, kind=kind,
                                            status=status, limit=limit)}


@router.get("/notes/stats")
def notes_stats():
    return {"stats": notes_store.stats()}


@router.post("/notes", status_code=201)
def create_note(n: NoteIn):
    nid = notes_store.add(project=n.project, kind=n.kind, title=n.title,
                          body=n.body, tags=n.tags, source_session="app")
    return notes_store.get(nid)


@router.patch("/notes/{note_id}")
def patch_note(note_id: int, p: NotePatch):
    note = notes_store.get(note_id)
    if not note:
        raise HTTPException(404, "note not found")
    if p.status is not None:
        notes_store.set_status(note_id, p.status)
    if p.body is not None:
        notes_store.update_body(note_id, p.body)
    return notes_store.get(note_id)


@router.delete("/notes/{note_id}", status_code=204)
def remove_note(note_id: int):
    if not notes_store.delete(note_id):
        raise HTTPException(404, "note not found")


# ── reminders ─────────────────────────────────────────────────────────────────

class ReminderIn(BaseModel):
    text: str
    due_at: float                 # unix timestamp
    project: str | None = None


@router.get("/reminders")
def list_reminders(limit: int = 100):
    return {"reminders": reminders_store.list_pending(limit=limit)}


@router.post("/reminders", status_code=201)
def create_reminder(r: ReminderIn):
    rid = reminders_store.add(r.text, r.due_at, project=r.project)
    return {"id": rid, "text": r.text, "due_at": r.due_at, "project": r.project}


@router.delete("/reminders/{reminder_id}", status_code=204)
def remove_reminder(reminder_id: int):
    if not reminders_store.delete(reminder_id):
        raise HTTPException(404, "reminder not found")


# ── diary (read) ──────────────────────────────────────────────────────────────

@router.get("/diary")
def diary(category: str | None = None, limit: int = 30):
    rows = (diary_store.by_category(category, limit) if category
            else diary_store.recent(limit))
    return {"entries": rows, "counts": diary_store.counts()}


# ── device registration (FCM push target) ─────────────────────────────────────

class DeviceIn(BaseModel):
    fcm_token: str
    platform: str = "android"
    label: str | None = None


@router.post("/devices")
def register_device(d: DeviceIn):
    devices_store.upsert(d.fcm_token, d.platform, d.label)
    return {"registered": True, "count": devices_store.count()}


# ── image upload / serve ──────────────────────────────────────────────────────

@router.post("/upload")
async def upload_image(request: Request, name: str = "image.jpg"):
    """Store a raw image body and return its server path. The app sends the file
    bytes directly (no multipart) with the original filename in `name`; the agent
    then reads the returned path via the claude tool.

    Returns {"path": "/abs/path", "name": "<original>"}.
    """
    ext = Path(name).suffix.lower()
    if ext not in _IMAGE_EXTS:
        ext = ".jpg"
    body = await request.body()
    if not body:
        raise HTTPException(400, "empty upload")
    if len(body) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"image too large (max {_MAX_UPLOAD_BYTES // (1024*1024)} MB)")
    dest = ensure_uploads_dir() / f"{uuid.uuid4().hex}{ext}"
    dest.write_bytes(body)
    return {"path": str(dest), "name": name}


@router.get("/file")
def serve_file(path: str):
    """Serve an image the app was told about (upload echo or an [image:] marker).
    Sandboxed to the uploads dir + active workspace via is_served_path."""
    p = Path(path).expanduser()
    if not is_served_path(p):
        raise HTTPException(404, "not found")
    return FileResponse(str(p))


@router.post("/push/test")
async def push_test():
    """Fire a test FCM push to every registered device."""
    if not fcm.available():
        raise HTTPException(503, "FCM not configured — missing service-account key")
    sent = await fcm.push_all(
        "Gajala", "🎉 Test push — notifications are working!", data={"type": "test"})
    return {"sent": sent, "devices": devices_store.count()}


# ── projects (switch active workspace) ────────────────────────────────────────

@router.get("/projects")
def list_projects():
    cur = str(cfg.WORKSPACE_DIR)
    return {
        "current_name": cfg.WORKSPACE_DIR.name,
        "projects": [{"name": p.name, "active": str(p) == cur} for p in _project_candidates()],
    }


class ProjectSwitch(BaseModel):
    name: str


@router.post("/projects/switch")
def switch_project(p: ProjectSwitch):
    _switch_workspace(p.name)   # sets cfg.WORKSPACE_DIR + persists state.json
    return {"current_name": cfg.WORKSPACE_DIR.name}


# ── active CLI sessions (continuity + "continue on Mac" handoff) ──────────────

# How to resume each engine's session in a terminal on the Mac.
_RESUME_CMD = {
    "claude": "claude --resume {sid}",
    "codex": "codex resume {sid}",
    "gemini": "gemini --resume {sid}",
}


@router.get("/sessions/active")
def active_cli_sessions():
    """The persistent CLI sessions for the active project — one per engine that
    supports reuse — plus a ready-to-paste 'continue on the Mac' command. Lets
    the app show which thread it's continuing and hand it off to the terminal."""
    from server.db import cli_sessions_store
    ws = str(cfg.WORKSPACE_DIR)
    out = []
    for engine, sid in cli_sessions_store.all_for(ws).items():
        tmpl = _RESUME_CMD.get(engine)
        out.append({"engine": engine, "session_id": sid,
                    "resume_cmd": tmpl.format(sid=sid) if tmpl else None})
    return {"workspace": cfg.WORKSPACE_DIR.name, "workspace_path": ws, "sessions": out}


# ── coding engine (pinned model for the chat) ─────────────────────────────────

@router.get("/model")
def get_model():
    from server import prefs
    return {
        "engine": prefs.get_coding_engine(),
        "options": list(prefs.CODING_ENGINES),
        "models": prefs.get_coding_models(),          # per-engine pinned model ('' = default)
        "backup_models": prefs.get_backup_models(),   # per-engine backup model ('' = none)
        "presets": prefs.model_presets(),             # per-engine selectable models
    }


class ModelSet(BaseModel):
    engine: str | None = None
    model: str | None = None
    backup: str | None = None


@router.post("/model")
def set_model(m: ModelSet):
    """Set the active coding engine and/or a model for an engine. All fields
    optional: {engine} switches engine; {engine, model} pins that engine's model;
    {engine, backup} pins its backup model (used if the primary run fails);
    {model}/{backup} alone apply to the currently active engine."""
    from server import prefs
    try:
        if m.engine is not None:
            prefs.set_coding_engine(m.engine)
        target = (m.engine if m.engine and m.engine != "auto"
                  else prefs.get_coding_engine())
        if m.model is not None and target in prefs.MODEL_ENGINES:
            prefs.set_coding_model(target, m.model)
        if m.backup is not None and target in prefs.MODEL_ENGINES:
            prefs.set_backup_model(target, m.backup)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"engine": prefs.get_coding_engine(),
            "models": prefs.get_coding_models(),
            "backup_models": prefs.get_backup_models()}


# ── usage (LLM provider quota/activity via codaur) ────────────────────────────

def _limit_is_current(limit: dict) -> bool:
    """A quota percentage stops being truthful once its window has reset."""
    resets_at = limit.get("resetsAt", limit.get("resets_at"))
    if resets_at is None:
        return True
    try:
        return float(resets_at) > time.time()
    except (TypeError, ValueError):
        return True


def _rate_pcts(rep: dict) -> tuple:
    """Extract (5-hour %, weekly %) from a codaur provider report.

    codaur moved captured rate limits into a `limitUsage[]` array (window "5h"
    / "7d" with `usedPercent`); older builds put them in
    `latestRateLimitSnapshot`. Read the new schema first, fall back to legacy.
    """
    primary = secondary = None
    for lu in rep.get("limitUsage") or []:
        if not _limit_is_current(lu):
            continue
        pct = lu.get("usedPercent")
        if pct is None:
            continue
        window = (lu.get("window") or "").lower()
        if "5h" in window or window.startswith("current"):
            primary = pct
        elif "7d" in window or "week" in window:
            secondary = pct
    if primary is None or secondary is None:            # legacy fallback
        snap = rep.get("latestRateLimitSnapshot") or {}
        legacy_primary = snap.get("primary") or {}
        legacy_secondary = snap.get("secondary") or {}
        if primary is None and _limit_is_current(legacy_primary):
            primary = legacy_primary.get("used_percent")
        if secondary is None and _limit_is_current(legacy_secondary):
            secondary = legacy_secondary.get("used_percent")
    return primary, secondary


_WINDOW_LABELS = {"5h": "5-hour", "7d": "weekly", "1d": "daily", "current": "current"}


def _limits(rep: dict) -> list:
    """Human-labeled rate-limit bars for the app — one per window a provider
    reports. Engines differ: Codex/Claude expose 5h+7d token windows, Gemini a
    single daily-request window, so the app renders whatever comes back."""
    out = []
    for lu in rep.get("limitUsage") or []:
        if not _limit_is_current(lu):
            continue
        pct = lu.get("usedPercent")
        if pct is None:
            continue
        window = (lu.get("window") or "").lower()
        label = _WINDOW_LABELS.get(window, window or "usage")
        if (lu.get("unit") or "") == "requests":
            label += " requests"
        detail = None
        if lu.get("used") is not None and lu.get("limit"):
            detail = f"{int(lu['used'])} / {int(lu['limit'])}"
        out.append({"label": label, "pct": round(float(pct), 1), "detail": detail})
    return out


_PLAN_DISPLAY = {
    "aipro": "Pro", "aiultra": "Ultra", "pro": "Pro", "plus": "Plus",
    "free": "Free", "standard": "Standard", "enterprise": "Enterprise",
    "max": "Max", "team": "Team",
}


def _plan_label(raw) -> str | None:
    """Normalize a plan value (native or configured) to a short chip label."""
    if not raw or str(raw).lower() == "null":
        return None
    return _PLAN_DISPLAY.get(str(raw).lower(), str(raw).capitalize())


def _codaur_plans() -> dict:
    """Plans the user configured via `codaur config set-plan` — used to label
    engines (Claude/Gemini) that don't carry a plan in their usage data."""
    try:
        cfg_file = Path.home() / ".config" / "codaur" / "config.json"
        data = _json.loads(cfg_file.read_text())
        return {k: (v or {}).get("plan") for k, v in data.items()}
    except Exception:
        return {}


@router.get("/usage")
def usage(response: Response):
    # Usage is inherently live data. Do not let a client or reverse proxy serve
    # an earlier Codaur report when Gajala polls this endpoint.
    response.headers["Cache-Control"] = "no-store"
    if shutil.which("codaur") is None:
        raise HTTPException(503, "codaur not installed")
    try:
        result = subprocess.run(["codaur", "--provider", "all", "--json"],
                                capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "unknown error").strip()
            raise HTTPException(502, f"codaur failed: {detail[:500]}")
        out = result.stdout
        brace = out.find("{")
        if brace < 0:
            raise HTTPException(502, "codaur returned no JSON")
        data = _json.loads(out[brace:])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"codaur failed: {e}")

    # Antigravity exposes no local token/limit data (protobuf blobs) — skip it.
    _EXCLUDE = {"antigravity"}
    configured_plans = _codaur_plans()

    providers = []
    for rep in data.get("reports", []):
        provider = rep.get("provider")
        if provider in _EXCLUDE:
            continue
        snap = rep.get("latestRateLimitSnapshot") or {}
        totals = rep.get("totals") or {}
        primary_pct, secondary_pct = _rate_pcts(rep)
        providers.append({
            "provider": provider,
            # Native plan (codex) if present, else the codaur-configured plan.
            "plan": _plan_label(snap.get("planType") or configured_plans.get(provider)),
            "primary_pct": primary_pct,
            "secondary_pct": secondary_pct,
            "today_tokens": totals.get("todayTokens"),
            "total_tokens": totals.get("tokens"),
            "threads": totals.get("threads"),
            # Activity fallback for engines that don't expose tokens (Antigravity).
            "events": totals.get("events"),
            # Generic labeled rate-limit bars (per-engine window shapes differ).
            "limits": _limits(rep),
        })

    # Hide providers with no signal at all (unused engines) so the screen shows
    # only the ones you actually run. Keep everything if nothing has data.
    def _has_signal(p: dict) -> bool:
        return (p["primary_pct"] is not None or p["secondary_pct"] is not None
                or bool(p["today_tokens"]) or bool(p["total_tokens"])
                or (p["threads"] or 0) > 0 or (p["events"] or 0) > 0)

    active = [p for p in providers if _has_signal(p)]
    return {"providers": active or providers}
