"""Project awareness and evidence-backed duplicate/overlap classification."""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

from server import config
from server.db import capability_store, deployment_store, night_queue_store

_STOP = {
    "a", "an", "and", "app", "as", "at", "be", "by", "for", "from", "gajala",
    "in", "into", "is", "it", "of", "on", "or", "our", "that", "the", "this",
    "to", "with", "should", "feature", "task", "implement", "implementation",
}
_EXTENSION = {"add", "improve", "enhance", "extend", "additional", "better", "show", "explain",
              "support", "optimize", "polish", "redesign", "refactor", "more"}
REGISTRY_VERSION = 3
_last_refresh = 0.0


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {word for word in words if len(word) > 2 and word not in _STOP}


def _text(job: dict) -> str:
    spec = job.get("spec_json") or {}
    return " ".join(str(value) for value in (
        spec.get("title") or job.get("task") or "", spec.get("outcome") or "",
        spec.get("context") or "", " ".join(spec.get("acceptance") or []),
    ))


def _title(job: dict) -> str:
    return str((job.get("spec_json") or {}).get("title") or job.get("task") or "")


def _score(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    # Containment rewards the common case where one task is a verbose refinement
    # of the same short request, while Jaccard penalizes unrelated extra scope.
    containment = intersection / min(len(a), len(b))
    jaccard = intersection / len(a | b)
    return round(0.65 * containment + 0.35 * jaccard, 4)


def _similarity(left_title: str, left_text: str,
                right_title: str, right_text: str) -> float:
    """Prefer the intended outcome title over generic work-order boilerplate."""
    title_score = _score(left_title, right_title)
    combined = round(.75 * title_score + .25 * _score(left_text, right_text), 4)
    if len(_tokens(left_title)) >= 2 and _tokens(left_title) == _tokens(right_title):
        return max(.96, combined)
    return combined


def _git_head() -> str | None:
    try:
        done = subprocess.run(
            ["git", "-C", str(config.REPO_DIR), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5)
        return done.stdout.strip() if done.returncode == 0 else None
    except Exception:
        return None


def _is_ancestor(commit: str | None, head: str | None) -> bool:
    if not commit or not head:
        return False
    try:
        done = subprocess.run(
            ["git", "-C", str(config.REPO_DIR), "merge-base", "--is-ancestor",
             commit, head], capture_output=True, text=True, timeout=5)
        return done.returncode == 0
    except Exception:
        return False


def _repo_project() -> str:
    # A verified deployment runs from a detached Git worktree. Its logical
    # project is still the owner repository (the parent of the common .git
    # directory), otherwise static Gajala capabilities never match queued work.
    try:
        done = subprocess.run(
            ["git", "-C", str(config.REPO_DIR), "rev-parse",
             "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, timeout=5)
        common = Path(done.stdout.strip()).resolve()
        if done.returncode == 0 and common.name == ".git":
            return str(common.parent)
    except Exception:
        pass
    return str(Path(config.REPO_DIR).resolve())


def refresh(*, force: bool = False) -> int:
    """Refresh authoritative sources. Static scanning is bounded to known files."""
    global _last_refresh
    now = time.time()
    if not force and now - _last_refresh < 300:
        return capability_store.count()
    head = _git_head()

    from server import prefs
    from server.skills import discover, manifest
    discover()
    skills = [{
        "key": f"skill:{item['name']}", "title": item["name"],
        "description": item.get("description") or item.get("help_line") or "",
        "evidence": [f"/{item.get('command') or item['name']}", "registered skill"],
        "status": "live" if prefs.is_skill_enabled(item["name"]) else "disabled",
        "verified_commit": head, "project": _repo_project(),
    } for item in manifest()]
    capability_store.replace_source("skill", skills)

    # API routes are the actual server surface, not documentation claims.
    try:
        from server.main import app
        routes = []
        for route in app.routes:
            path = getattr(route, "path", "")
            methods = sorted(getattr(route, "methods", []) or [])
            if path.startswith(("/api/", "/run", "/skills")):
                routes.append({
                    "key": f"api:{','.join(methods)}:{path}",
                    "title": path.replace("/api/", "").replace("/", " "),
                    "description": f"{'/'.join(methods)} {path}",
                    "evidence": [f"server route {'/'.join(methods)} {path}"],
                    "verified_commit": head, "project": _repo_project(),
                })
        capability_store.replace_source("api", routes)
    except Exception:
        pass

    screen_dir = Path(config.REPO_DIR) / "clients" / "gajala" / "lib" / "screens"
    screens = []
    for path in sorted(screen_dir.glob("*_screen.dart")) if screen_dir.is_dir() else []:
        name = path.stem.removesuffix("_screen").replace("_", " ")
        screens.append({
            "key": f"screen:{path.stem}", "title": name,
            "description": f"Gajala {name} screen",
            "evidence": [str(path.relative_to(config.REPO_DIR))],
            "verified_commit": head, "project": _repo_project(),
        })
    capability_store.replace_source("screen", screens)

    # Native launcher widgets are important Gajala surfaces but do not appear
    # in Flutter's screens directory. Extract their user-visible tile labels so
    # work such as "Brain dump widget" matches the feature, not just a filename.
    widget_root = Path(config.REPO_DIR) / "clients" / "gajala" / "android" / "app" / "src" / "main"
    widgets = []
    for path in sorted((widget_root / "res" / "layout").glob("widget_*.xml")) \
            if (widget_root / "res" / "layout").is_dir() else []:
        try:
            labels = re.findall(r'android:text="([^"]+)"', path.read_text())
        except OSError:
            labels = []
        title = path.stem.replace("widget_", "").replace("_", " ") + " widget"
        widgets.append({
            "key": f"widget:{path.stem}", "title": title,
            "description": "Android home-screen widget: " + ", ".join(labels),
            "evidence": [str(path.relative_to(config.REPO_DIR))],
            "verified_commit": head, "project": _repo_project(),
        })
        for label in labels:
            slug = "-".join(re.findall(r"[a-z0-9]+", label.lower()))
            widgets.append({
                "key": f"widget-action:{path.stem}:{slug}", "title": label,
                "description": f"{label} action in the Android home-screen widget",
                "evidence": [str(path.relative_to(config.REPO_DIR))],
                "verified_commit": head, "project": _repo_project(),
            })
    if widgets:
        widgets.append({
            "key": "widget:android-home-screen", "title": "Android home-screen widgets",
            "description": "Native Gajala actions and Codaur usage widgets",
            "evidence": ["clients/gajala/android/app/src/main/res/layout"],
            "verified_commit": head, "project": _repo_project(),
        })
    capability_store.replace_source("widget", widgets)

    test_file = Path(config.REPO_DIR) / "server" / "tests" / "test_smoke.py"
    tests = []
    try:
        for name in re.findall(r"^def (test_[a-zA-Z0-9_]+)", test_file.read_text(), re.MULTILINE):
            title = name.removeprefix("test_").replace("_", " ")
            tests.append({
                "key": f"test:{name}", "title": title, "description": title,
                "evidence": [f"server/tests/test_smoke.py::{name}"],
                "status": "evidence",
                "verified_commit": head, "project": _repo_project(),
            })
    except OSError:
        pass
    capability_store.replace_source("test", tests)

    shipped = []
    for job in night_queue_store.list_jobs(limit=500):
        if not night_queue_store._dependency_satisfied(job):
            continue
        deployment = deployment_store.latest(source="queue", ref_id=job["id"])
        # Research completion is itself the deliverable. Coding work is evidence
        # only when its deployed commit is in the current verified history; old
        # ledger rows that said live after a failed push must not close new work.
        spec = job.get("spec_json") or {}
        if (spec.get("work_type") != "research" and not (
                deployment and deployment.get("state") == "live"
                and _is_ancestor(deployment.get("deployed_sha"), head))):
            continue
        shipped.append({
            "key": f"job:{job['id']}", "title": spec.get("title") or job["task"],
            "description": _text(job), "evidence": [
                f"queue task #{job['id']} {job['status']}",
                *(job.get("files_changed") or [])[:8],
            ], "ref_id": job["id"], "verified_commit": (
                (deployment or {}).get("deployed_sha") or head),
            "project": str(Path(job["project"]).resolve()),
        })
    capability_store.replace_source("shipped_job", shipped)
    _last_refresh = now
    return capability_store.count()


def assess(job_id: int, *, apply: bool = True) -> dict:
    """Classify a job against active work and live capability evidence."""
    refresh()
    job = night_queue_store.get(job_id)
    if not job:
        raise LookupError(f"no job #{job_id}")
    text = _text(job)
    title = _title(job)
    project = str(Path(job["project"]).resolve())
    tokens = _tokens(text)
    candidates: list[dict] = []

    for other in night_queue_store.list_jobs(limit=500):
        if (other["id"] == job_id or other["status"] == "closed"
                or str(Path(other["project"]).resolve()) != project):
            continue
        score = _similarity(title, text, _title(other), _text(other))
        if score >= .45:
            candidates.append({
                "kind": "task", "id": other["id"],
                "title": (other.get("spec_json") or {}).get("title") or other["task"],
                "status": other["status"], "score": score,
                "evidence": [f"queue task #{other['id']} is {other['status']}"],
            })
    for capability in capability_store.list_all(status=None, project=project):
        score = _similarity(
            title, text, capability["title"],
            f"{capability['title']} {capability['description']}")
        if score >= .45:
            candidates.append({
                "kind": "capability", "key": capability["key"],
                "id": capability.get("ref_id"), "title": capability["title"],
                "status": capability["status"], "source": capability["source"],
                "score": score,
                "evidence": capability["evidence"],
            })
    candidates.sort(key=lambda value: value["score"], reverse=True)
    # Tests and documentation are useful context, but only a live surface or an
    # actual task may own an outcome. This prevents a test name alone from
    # closing or blocking new work.
    actionable = [value for value in candidates if
                  value["kind"] == "task" or value["status"] == "live"]
    best = actionable[0] if actionable else None
    extension = bool(tokens & _EXTENSION)
    classification, confidence = "new", 1.0 if not best else max(.5, 1 - best["score"])
    action = "Proceed normally; no material overlap was found."
    if best:
        completed = ((best["kind"] == "capability" and best["status"] == "live")
                     or best["status"] in ("shipped", "completed"))
        active = best["kind"] == "task" and not completed
        if best["score"] >= .92 and completed and not extension:
            classification, confidence = "already_implemented", best["score"]
            action = "Close recoverably because the verified capability already exists."
        elif best["score"] >= .92 and active and not extension:
            classification, confidence = "duplicate", best["score"]
            action = f"Close recoverably; task #{best['id']} already owns this outcome."
        elif best["score"] >= .62 and completed and extension:
            classification, confidence = "extension", best["score"]
            action = "Proceed, explicitly building on the existing capability."
        elif best["score"] >= .68 and active:
            classification, confidence = "conflict", best["score"]
            action = f"Wait behind task #{best['id']} to avoid parallel overlapping work."
        elif (best["score"] >= .55 and
              (best["kind"] == "task" or best.get("source") == "shipped_job")):
            classification, confidence = "unclear", best["score"]
            action = "Keep visible and held until refinement distinguishes the outcome."
    result = {
        "registry_version": REGISTRY_VERSION,
        "classification": classification, "confidence": round(confidence, 3),
        "match": best, "candidates": candidates[:5], "action": action,
        "checked_at": time.time(), "registry_count": capability_store.count(),
    }
    if not apply:
        return result

    fields: dict = {"awareness_json": result, "awareness_checked_at": result["checked_at"]}
    evidence = "; ".join((best or {}).get("evidence") or [])[:500]
    if classification in ("duplicate", "already_implemented") and confidence >= .92:
        reason = f"Supervisor: {classification.replace('_', ' ')} — {best['title']}. Evidence: {evidence}"
        night_queue_store.update(job_id, awareness_json=result)
        night_queue_store.close(job_id, reason)
        return result
    if classification == "conflict" and best and best.get("id"):
        dependencies = list(job.get("depends_on") or [])
        if best["id"] not in dependencies:
            dependencies.append(best["id"])
        fields.update(depends_on=dependencies,
                      blocker_reason=f"Overlaps active task #{best['id']}.",
                      next_action=f"Supervisor will reassess after task #{best['id']} finishes.")
    elif classification == "unclear" and job.get("status") == "queued":
        fields.update(status="held", tag="mine",
                      blocker_reason=f"Possible overlap with {best['title']}.",
                      next_action="Refine the distinction before running duplicate work.")
    elif classification == "extension" and best:
        fields.update(next_action=f"Proceed as an extension of {best['title']}; preserve existing behavior.")
    night_queue_store.update(job_id, **fields)
    return result
