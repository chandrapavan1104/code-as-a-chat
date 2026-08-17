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
import threading
import uuid
from pathlib import Path
import psutil
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from server import config as cfg
from server import fcm, workspace
from server.db import notes_store, diary_store, reminders_store
from server.db import devices_store
from server.db import store as memory
from server.media import ensure_uploads_dir, is_served_path
from server.work_orders import WorkOrderSpec

router = APIRouter(prefix="/api", tags=["app"])

# Cap an inbound image upload. Phone screenshots/photos are a few MB; 25 MB is
# generous headroom without inviting abuse.
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".bmp"}

# The screen, dashboard, and Android widget can refresh Codaur on the same tick.
# Coalesce those expensive CLI calls instead of racing provider session files.
_CODAUR_LOCK = threading.Lock()
_CODAUR_CACHE: tuple[float, dict] | None = None
_CODAUR_CACHE_SECONDS = 5
_CODAUR_STALE_FALLBACK_SECONDS = 120


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


# ── skills ───────────────────────────────────────────────────────────────────

class SkillToggleRequest(BaseModel):
    enabled: bool


@router.get("/skills")
def list_skills():
    """List all registered skills with their enabled/disabled state."""
    from server.skills import registry
    from server import prefs

    skills = []
    for name in sorted(registry.keys()):
        skill = registry[name]
        enabled = prefs.is_skill_enabled(name)
        skills.append({
            "name": name,
            "description": skill.description,
            "help_line": getattr(skill, "menu_line", skill.description),
            "expose_to_agent": getattr(skill, "expose_to_agent", True),
            "passthrough": getattr(skill, "passthrough", False),
            "final_output": getattr(skill, "final_output", False),
            "enabled": enabled,
        })
    return {"skills": skills}


@router.post("/skills/{skill_name}")
def toggle_skill(skill_name: str, request: SkillToggleRequest):
    """Enable or disable a skill."""
    from server.skills import registry
    from server import prefs

    # Validate that the skill exists
    if skill_name not in registry:
        raise HTTPException(404, f"skill '{skill_name}' not found")

    # Set the enabled state
    prefs.set_skill_enabled(skill_name, request.enabled)

    return {
        "name": skill_name,
        "enabled": request.enabled,
        "message": f"Skill '{skill_name}' is now {'enabled' if request.enabled else 'disabled'}"
    }


# ── notes ─────────────────────────────────────────────────────────────────────

class NoteIn(BaseModel):
    project: str | None = None
    kind: str = "note"
    title: str
    body: str = ""
    tags: list[str] = []


class NotePatch(BaseModel):
    status: str | None = None   # open | done (mark complete)
    body: str | None = None


class NoteClose(BaseModel):
    reason: str = ""  # optional reason for closure


class NoteConvertToQueue(BaseModel):
    spec: WorkOrderSpec | None = None  # omitted = safe Draft from the rough note
    project: str | None = None  # target project; defaults to note's project
    tag: str = "mine"  # always create as 'mine' (held) for review
    engine: str = "auto"


@router.get("/notes")
def list_notes(status: str | None = "open", kind: str | None = None,
               project: str | None = None, limit: int = 100):
    from server.db import night_queue_store
    notes = notes_store.list_notes(project=project, kind=kind,
                                   status=status, limit=limit)
    linked = {}
    for job in night_queue_store.list_jobs(limit=500):
        note_id = job.get("source_note_id")
        if note_id and note_id not in linked:
            linked[note_id] = {"id": job["id"], "status": job["status"]}
    for note in notes:
        note["queue_job"] = linked.get(note["id"])
    return {"notes": notes}


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


@router.post("/notes/{note_id}/close")
def close_note(note_id: int, body: NoteClose):
    note = notes_store.get(note_id)
    if not note:
        raise HTTPException(404, "note not found")
    reason = body.reason.strip() or "Closed from app"
    if not notes_store.close(note_id, reason):
        raise HTTPException(409, "could not close note")
    return notes_store.get(note_id)


@router.post("/notes/{note_id}/reopen")
def reopen_note(note_id: int):
    note = notes_store.get(note_id)
    if not note:
        raise HTTPException(404, "note not found")
    if not notes_store.reopen(note_id):
        raise HTTPException(409, "note is not closed")
    return notes_store.get(note_id)


@router.post("/notes/{note_id}/convert-to-queue", status_code=201)
def convert_note_to_queue(note_id: int, body: NoteConvertToQueue):
    """Convert an open todo to one linked, recoverable Draft work order."""
    from server.db import night_queue_store
    from server.work_orders import from_task
    note = notes_store.get(note_id)
    if not note:
        raise HTTPException(404, "note not found")
    if note["kind"] != "todo":
        raise HTTPException(400, "only todos can be converted to queue jobs")
    if note["status"] != "open":
        raise HTTPException(409, "only open todos can be converted")

    project = _resolve_project_path(body.project or note["project"])
    rough = "\n\n".join(value for value in (note["title"], note["body"]) if value)
    spec = body.spec or from_task(rough)
    if body.spec is not None and not spec.is_complete:
        raise HTTPException(400, "spec must be complete: title, outcome, plan, "
                          "policy, acceptance, test_handoff all required")

    # Create the queue job, linked to this note and held for review
    task = spec.as_task()
    jid = night_queue_store.add(
        project=project, task=task, tag="mine", engine=body.engine,
        spec=spec.model_dump(), source_note_id=note_id)

    # Conversion is a move, not a copy. Archive the source recoverably so the
    # same rough item cannot be converted twice; reopening remains available.
    notes_store.close(note_id, f"Converted to queue task #{jid}")

    return _job_view(night_queue_store.get(jid))


@router.delete("/notes/{note_id}", status_code=204)
def remove_note(note_id: int):
    # Hard delete is maintenance-only; never expose through app.
    # Keeping this endpoint for CLI use only, never call from Flutter.
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
    """Every candidate project with the facts needed to tell them apart: where it
    actually lives, whether it is a repo, which branch, and how stale it is. A
    bare name is not enough when ~/Projects holds thirty directories."""
    current = workspace.active()
    return {
        "current_name": current.name,
        "current_path": str(current),
        "current_display_path": workspace.prettify(current),
        "parent_dir": workspace.prettify(workspace.parent_dir()),
        "projects": workspace.describe_all(),
    }


class ProjectSwitch(BaseModel):
    name: str


@router.post("/projects/switch")
def switch_project(p: ProjectSwitch):
    """Move the default project for new threads. Fails LOUDLY: this used to
    discard the resolver's answer and return 200 with the *old* name, so the app
    reported "Switched to …" for a switch that never happened."""
    target = workspace.resolve(p.name)
    if target is None:
        raise HTTPException(
            status_code=404,
            detail={
                "message": f"No project matches '{p.name}'.",
                "suggestions": workspace.suggestions(p.name),
            },
        )
    previous = workspace.active()
    workspace.persist_default(target)
    try:
        from server.skills.context import ensure_context
        ensure_context(target)
    except Exception:
        pass
    return {
        "current_name": target.name,
        "previous_name": previous.name,
        "project": workspace.describe(target, is_active=True),
    }


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
    from server.db import cli_sessions_store, native_sessions
    ws = str(workspace.active())
    sessions = cli_sessions_store.all_for(ws)
    # Reconcile before displaying: a terminal-started native thread should be
    # visible immediately, not only after Gajala has completed another turn.
    for engine in _RESUME_CMD:
        native = native_sessions.latest(ws, engine)
        if native:
            sessions[engine] = native[0]
            cli_sessions_store.set(ws, engine, native[0])
    out = []
    for engine, sid in sorted(sessions.items()):
        tmpl = _RESUME_CMD.get(engine)
        out.append({"engine": engine, "session_id": sid,
                    "resume_cmd": tmpl.format(sid=sid) if tmpl else None})
    return {"workspace": workspace.active().name, "workspace_path": ws, "sessions": out}


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


# ── error/crash capture (the fix agent's eyes) ────────────────────────────────

class ClientError(BaseModel):
    kind: str = "flutter"
    message: str
    stack: str | None = None
    context: dict | None = None


@router.post("/clienterror")
def report_client_error(e: ClientError):
    """The Gajala app POSTs its crashes/errors here so the fix agent can see them."""
    from server.db import errors_store
    errors_store.add("app", e.kind, e.message, detail=e.stack or "",
                     context=e.context or {})
    return {"ok": True}


@router.get("/errors")
def list_errors(limit: int = 30, source: str | None = None):
    from server.db import errors_store
    return {"errors": errors_store.recent(limit, source)}


# ── app self-update ───────────────────────────────────────────────────────────

@router.get("/appversion")
def app_version():
    """Latest built APK's versionCode + download URL, from the manifest the build
    skill writes. The app compares its own buildNumber to offer an in-app update."""
    meta_path = Path(cfg.APK_DEST).with_name("apk_version.json")
    try:
        meta = _json.loads(meta_path.read_text())
    except Exception:
        meta = {"build_number": 0, "version_name": "", "url": cfg.APK_URL}
    meta.setdefault("url", cfg.APK_URL)
    return meta


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


def _codaur_report() -> dict:
    """Return one coalesced Codaur report, with a bounded last-good fallback."""
    global _CODAUR_CACHE
    now = time.monotonic()
    cached = _CODAUR_CACHE
    if cached and now - cached[0] <= _CODAUR_CACHE_SECONDS:
        return cached[1]

    # A concurrent caller should reuse recent data immediately. Only the first
    # ever request waits, because there is no useful report to serve yet.
    acquired = _CODAUR_LOCK.acquire(blocking=False)
    if not acquired:
        if cached and now - cached[0] <= _CODAUR_STALE_FALLBACK_SECONDS:
            return cached[1]
        with _CODAUR_LOCK:
            cached = _CODAUR_CACHE
            if cached:
                return cached[1]
            raise RuntimeError("initial usage refresh did not produce a report")

    try:
        # Double-check after acquiring: a previous owner may just have finished.
        cached = _CODAUR_CACHE
        now = time.monotonic()
        if cached and now - cached[0] <= _CODAUR_CACHE_SECONDS:
            return cached[1]
        try:
            result = subprocess.run(
                ["codaur", "--provider", "all", "--json"],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "unknown error").strip()
                raise RuntimeError(detail[:500])
            brace = result.stdout.find("{")
            if brace < 0:
                raise RuntimeError("codaur returned no JSON")
            data = _json.loads(result.stdout[brace:])
            _CODAUR_CACHE = (time.monotonic(), data)
            return data
        except Exception:
            cached = _CODAUR_CACHE
            if (cached and time.monotonic() - cached[0]
                    <= _CODAUR_STALE_FALLBACK_SECONDS):
                return cached[1]
            raise
    finally:
        _CODAUR_LOCK.release()


@router.get("/usage")
def usage(response: Response):
    # Usage is inherently live data. Do not let a client or reverse proxy serve
    # an earlier Codaur report when Gajala polls this endpoint.
    response.headers["Cache-Control"] = "no-store"
    if shutil.which("codaur") is None:
        raise HTTPException(503, "codaur not installed")
    try:
        data = _codaur_report()
    except Exception as e:
        raise HTTPException(502, f"codaur failed: {e}") from e

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
            "plan": ("Local" if provider == "qwen" else
                     _plan_label(snap.get("planType") or configured_plans.get(provider))),
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


# ── Night Shift queue (Tasks tab) ─────────────────────────────────────────────

def _job_view(j: dict) -> dict:
    """Trim a night_queue_store row to what the app renders."""
    from pathlib import Path as _P
    from server.db import deployment_store, night_queue_store, routing_recommendations_store
    spec = j.get("spec_json") or {}
    try:
        deployment = deployment_store.latest(source="queue", ref_id=j["id"])
    except OSError:
        deployment = None
    except Exception:  # ledger visibility must never break the Tasks screen
        deployment = None
    # Shadow-mode routing recommendation (if available)
    routing_rec = None
    try:
        routing_rec = routing_recommendations_store.get(j["id"])
    except Exception:
        pass
    from server.queue_supervisor import job_explanation
    return {
        "id": j["id"],
        "project": j["project"],
        "project_name": _P(j["project"]).name,
        "task": j["task"],
        "title": spec.get("title") or j["task"].splitlines()[0],
        "spec": spec,
        "readiness": spec.get("readiness", "draft"),
        "tag": j["tag"],
        "engine": j.get("engine_used") or j["engine"],
        "status": j["status"],
        "branch": j.get("branch"),
        "summary": j.get("summary"),
        "files_changed": j.get("files_changed") or [],
        "tokens_total": j.get("tokens_total") or 0,
        "created_at": j.get("created_at"),
        "ended_at": j.get("ended_at"),
        "depends_on": j.get("depends_on") or [],
        "dependencies": night_queue_store.dependency_status(j),
        "blocked_by": night_queue_store.blocked_by(j),
        "closed_at": j.get("closed_at"),
        "close_reason": j.get("close_reason"),
        "previous_status": j.get("previous_status"),
        "closure_history": j.get("closure_history") or [],
        "source_note_id": j.get("source_note_id"),
        "deployment": deployment,
        "routing_recommendation": routing_rec,  # shadow-mode recommendation
        "awareness": j.get("awareness_json") or {},
        "supervision": job_explanation(j),
    }


@router.get("/deployments")
def deployment_list():
    from server.db import deployment_store
    return {"items": deployment_store.list_recent()}


class QueueJobIn(BaseModel):
    task: str = ""
    spec: WorkOrderSpec | None = None
    depends_on: list[int] = Field(default_factory=list)
    project: str | None = None          # name or path; defaults to active workspace
    tag: str = "auto"                   # auto | mine
    engine: str = "auto"                # auto | claude | codex | gemini
    priority: int = 0


class QueueTag(BaseModel):
    tag: str


class QueueEngine(BaseModel):
    engine: str


class QueueClose(BaseModel):
    reason: str


class QueueRefineIn(BaseModel):
    allow_cloud: bool = False
    instructions: str = Field(default="", max_length=2000)


class QueueEditIn(BaseModel):
    spec: WorkOrderSpec
    depends_on: list[int] | None = None
    project: str | None = None
    engine: str | None = None


class QueueSettingsIn(BaseModel):
    enabled: bool | None = None
    start: str | None = None
    end: str | None = None
    engines: str | None = None
    quota_stop_pct: int | None = None
    max_jobs: int | None = None
    token_budget: int | None = None


@router.get("/queue")
def queue_list(status: str | None = None):
    from server import prefs
    from server.db import night_queue_store
    from server.queue_supervisor import health_snapshot
    jobs = night_queue_store.list_jobs(status=status, limit=200)
    return {"jobs": [_job_view(j) for j in jobs], "settings": prefs.night_settings(),
            "health": health_snapshot()}


@router.get("/capabilities")
def capabilities_list():
    """Evidence the supervisor uses when deciding whether work already exists."""
    from server.capability_registry import refresh
    from server.db import capability_store
    refresh()
    items = capability_store.list_all()
    return {"count": len(items), "items": items}


@router.post("/queue/supervise")
async def queue_supervise():
    """Manual nudge from Gajala; the same audit also runs every minute."""
    from server.queue_supervisor import health_snapshot, supervise_once
    actions = await supervise_once()
    return {"actions": actions, "health": health_snapshot()}


@router.post("/queue", status_code=201)
def queue_add(body: QueueJobIn):
    from server.db import night_queue_store
    from server.work_orders import from_task
    project = _resolve_project_path(body.project)
    spec = body.spec or from_task(body.task)
    task = spec.as_task() if body.spec is not None else body.task.strip()
    if not task:
        raise HTTPException(400, "task or work-order spec is required")
    missing = [job_id for job_id in body.depends_on
               if night_queue_store.get(job_id) is None]
    if missing:
        raise HTTPException(400, f"unknown dependencies: {missing}")
    jid = night_queue_store.add(
        project=project, task=task, tag=body.tag, engine=body.engine,
        priority=body.priority, spec=spec.model_dump(), depends_on=body.depends_on)
    from server.capability_registry import assess
    try:
        assess(jid)
    except Exception:
        pass  # awareness is advisory; it must never block task capture
    return _job_view(night_queue_store.get(jid))


@router.post("/queue/{job_id}/run")
async def queue_run(job_id: int):
    from server import night_shift
    from server.db import night_queue_store
    existing = night_queue_store.get(job_id)
    if existing and existing["status"] == "closed":
        raise HTTPException(409, "reopen the job before running it")
    if existing and not night_queue_store.is_refined(existing):
        raise HTTPException(409, "refine this draft before running it")
    blocked = night_queue_store.blocked_by(existing) if existing else []
    if blocked:
        raise HTTPException(409, f"blocked by unshipped jobs: {blocked}")
    job = await night_shift.run_now(job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    return _job_view(job)


@router.post("/queue/{job_id}/stop")
def queue_stop(job_id: int):
    from server import night_shift
    from server.db import night_queue_store
    if not night_shift.stop_job(job_id):
        raise HTTPException(409, "job is not running or already finished")
    return _job_view(night_queue_store.get(job_id))


@router.post("/queue/{job_id}/ship")
def queue_ship(job_id: int):
    from server.skills.queue import _ship
    return {"result": _ship(job_id)}


@router.post("/queue/{job_id}/tag")
def queue_tag(job_id: int, body: QueueTag):
    from server.db import night_queue_store
    if body.tag not in ("auto", "mine"):
        raise HTTPException(400, "tag must be auto or mine")
    j = night_queue_store.get(job_id)
    if not j:
        raise HTTPException(404, "no such job")
    if body.tag == "auto" and not night_queue_store.is_refined(j):
        raise HTTPException(409, "refine this draft before making it automatic")
    fields = {"tag": body.tag}
    if j["status"] in ("queued", "held"):
        fields["status"] = "held" if body.tag == "mine" else "queued"
    night_queue_store.update(job_id, **fields)
    return _job_view(night_queue_store.get(job_id))


@router.post("/queue/{job_id}/engine")
def queue_engine(job_id: int, body: QueueEngine):
    from server.db import night_queue_store
    if body.engine not in ("auto", "claude", "codex", "gemini"):
        raise HTTPException(400, "engine must be auto, claude, codex, or gemini")
    job = night_queue_store.get(job_id)
    if not job:
        raise HTTPException(404, "no such job")
    if job["status"] in ("running", "closed"):
        raise HTTPException(409, f"cannot change engine on a {job['status']} job")
    night_queue_store.update(job_id, engine=body.engine, engine_used=None)
    return _job_view(night_queue_store.get(job_id))


@router.post("/queue/{job_id}/refine")
async def queue_refine(job_id: int, body: QueueRefineIn):
    from server.db import night_queue_store
    from server.work_order_refiner import refine_job
    try:
        await refine_job(
            job_id, allow_cloud=body.allow_cloud, instructions=body.instructions)
        from server.capability_registry import assess
        try:
            assess(job_id)
        except Exception:
            pass
        return _job_view(night_queue_store.get(job_id))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.patch("/queue/{job_id}")
def queue_edit(job_id: int, body: QueueEditIn):
    from server.db import night_queue_store
    from server.work_orders import mark_refined, migrate_spec
    job = night_queue_store.get(job_id)
    if not job:
        raise HTTPException(404, "no such job")
    if job["status"] in ("running", "closed"):
        raise HTTPException(409, f"cannot edit a {job['status']} job")
    deps = body.depends_on if body.depends_on is not None else job.get("depends_on", [])
    if job_id in deps:
        raise HTTPException(400, "a job cannot depend on itself")
    missing = [dep for dep in deps if night_queue_store.get(dep) is None]
    if missing:
        raise HTTPException(400, f"unknown dependencies: {missing}")
    if body.engine is not None and body.engine not in ("auto", "claude", "codex", "gemini"):
        raise HTTPException(400, "invalid engine")
    spec = migrate_spec(body.spec.model_dump(), body.spec.source_text or job["task"])
    if spec.is_complete:
        mark_refined(spec, provider="manual")
    else:
        spec.readiness = "draft"
        spec.refined_at = None
        spec.refined_by = None
    fields = {
        "task": spec.as_task(), "spec_json": spec.model_dump(),
        "depends_on": deps, "status": "held", "tag": "mine",
        "summary": "Edited manually; held for review.",
    }
    if body.project is not None:
        fields["project"] = _resolve_project_path(body.project)
    if body.engine is not None:
        fields["engine"] = body.engine
        fields["engine_used"] = None
    night_queue_store.update(job_id, **fields)
    from server.capability_registry import assess
    try:
        assess(job_id)
    except Exception:
        pass
    return _job_view(night_queue_store.get(job_id))


@router.post("/queue/{job_id}/close")
def queue_close(job_id: int, body: QueueClose):
    from server.db import night_queue_store
    try:
        if not night_queue_store.close(job_id, body.reason):
            raise HTTPException(404, "no such job")
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _job_view(night_queue_store.get(job_id))


@router.post("/queue/{job_id}/reopen")
def queue_reopen(job_id: int):
    from server.db import night_queue_store
    if not night_queue_store.reopen(job_id):
        raise HTTPException(409, "job is not closed")
    return _job_view(night_queue_store.get(job_id))


@router.get("/queue/settings")
def queue_settings():
    from server import prefs
    return prefs.night_settings()


@router.post("/queue/settings")
def queue_settings_set(body: QueueSettingsIn):
    from server import prefs
    return prefs.set_night_settings(**body.model_dump(exclude_none=True))


def _resolve_project_path(name: str | None) -> str:
    """A project name/substring or path → absolute path.

    An omitted name means "the active workspace". A name that does NOT resolve
    is an error, not a shrug: this used to fall back to the active workspace,
    which quietly ran queued coding jobs against whatever repo happened to be
    current.
    """
    if not name or not name.strip():
        return str(workspace.active())
    p = workspace.resolve(name)
    if p is None:
        raise HTTPException(
            status_code=400,
            detail={
                "message": f"No project matches '{name}'.",
                "suggestions": workspace.suggestions(name),
            },
        )
    return str(p)


# ── Notifications inbox (Alerts tab) ──────────────────────────────────────────

def _notif_view(n: dict) -> dict:
    return {
        "id": n["id"], "type": n["type"], "title": n["title"], "body": n["body"],
        "status": n["status"], "needs_response": bool(n["needs_response"]),
        "response": n.get("response"), "ref_kind": n.get("ref_kind"),
        "ref_id": n.get("ref_id"), "created_at": n.get("created_at"),
    }


class NotifResponse(BaseModel):
    response: str


@router.get("/notifications")
def notifications_list(status: str | None = None, limit: int = 60):
    from server.db import notifications_store
    items = notifications_store.list(status=status, limit=limit)
    return {"items": [_notif_view(n) for n in items],
            "unread": notifications_store.unread_count()}


@router.post("/notifications/{notif_id}/read")
def notifications_read(notif_id: int):
    from server.db import notifications_store
    notifications_store.mark_read(notif_id)
    return {"unread": notifications_store.unread_count()}


@router.post("/notifications/read_all")
def notifications_read_all():
    from server.db import notifications_store
    notifications_store.mark_all_read()
    return {"unread": 0}


@router.post("/notifications/{notif_id}/respond")
async def notifications_respond(notif_id: int, body: NotifResponse):
    from server import night_shift
    from server.db import notifications_store
    n = notifications_store.get(notif_id)
    if not n:
        raise HTTPException(404, "no such notification")
    notifications_store.set_response(notif_id, body.response)
    result: dict = {"ok": True}
    # A queue_input question → feed the answer to the job and re-run it now.
    if n.get("type") == "queue_input" and n.get("ref_id"):
        job = night_shift.respond_to_job(int(n["ref_id"]), body.response)
        if job is not None:
            await night_shift.run_now(int(n["ref_id"]))
            result["job"] = _job_view(job)
    return result


@router.delete("/notifications/{notif_id}", status_code=204)
def notifications_dismiss(notif_id: int):
    from server.db import notifications_store
    notifications_store.dismiss(notif_id)
