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
import psutil
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from server import config as cfg
from server import fcm
from server.db import notes_store, diary_store, reminders_store
from server.db import devices_store
from server.db import store as memory
from server.skills.projects import _candidates as _project_candidates, _switch_view as _switch_workspace

router = APIRouter(prefix="/api", tags=["app"])


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


# ── usage (LLM provider quota/activity via codaur) ────────────────────────────

def _rate_pcts(rep: dict) -> tuple:
    """Extract (5-hour %, weekly %) from a codaur provider report.

    codaur moved captured rate limits into a `limitUsage[]` array (window "5h"
    / "7d" with `usedPercent`); older builds put them in
    `latestRateLimitSnapshot`. Read the new schema first, fall back to legacy.
    """
    primary = secondary = None
    for lu in rep.get("limitUsage") or []:
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
        primary = primary if primary is not None else (snap.get("primary") or {}).get("used_percent")
        secondary = secondary if secondary is not None else (snap.get("secondary") or {}).get("used_percent")
    return primary, secondary


@router.get("/usage")
def usage():
    if shutil.which("codaur") is None:
        raise HTTPException(503, "codaur not installed")
    try:
        out = subprocess.run(["codaur", "--provider", "all", "--json"],
                             capture_output=True, text=True, timeout=60).stdout
        brace = out.find("{")
        data = _json.loads(out[brace:]) if brace >= 0 else {}
    except Exception as e:
        raise HTTPException(500, f"codaur failed: {e}")

    # Antigravity exposes no local token/limit data (protobuf blobs) — skip it.
    _EXCLUDE = {"antigravity"}

    providers = []
    for rep in data.get("reports", []):
        if rep.get("provider") in _EXCLUDE:
            continue
        snap = rep.get("latestRateLimitSnapshot") or {}
        totals = rep.get("totals") or {}
        primary_pct, secondary_pct = _rate_pcts(rep)
        providers.append({
            "provider": rep.get("provider"),
            "plan": snap.get("planType") or rep.get("plan"),
            "primary_pct": primary_pct,
            "secondary_pct": secondary_pct,
            "today_tokens": totals.get("todayTokens"),
            "total_tokens": totals.get("tokens"),
            "threads": totals.get("threads"),
            # Activity fallback for engines that don't expose tokens (Antigravity).
            "events": totals.get("events"),
        })

    # Hide providers with no signal at all (unused engines) so the screen shows
    # only the ones you actually run. Keep everything if nothing has data.
    def _has_signal(p: dict) -> bool:
        return (p["primary_pct"] is not None or p["secondary_pct"] is not None
                or bool(p["today_tokens"]) or bool(p["total_tokens"])
                or (p["threads"] or 0) > 0 or (p["events"] or 0) > 0)

    active = [p for p in providers if _has_signal(p)]
    return {"providers": active or providers}
