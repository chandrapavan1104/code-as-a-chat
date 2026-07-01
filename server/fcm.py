"""
Outbound push to the Gajala Android app via Firebase Cloud Messaging (HTTP v1).

The counterpart to server.notify (Telegram): where notify messages you on
Telegram, this delivers a native push notification to every device that has
registered its FCM token through /api/devices.

Auth uses the service-account key at config.FCM_SERVICE_ACCOUNT. If that file is
missing the whole module no-ops gracefully, so the server (and Telegram push)
keep working without Firebase configured.
"""

import asyncio
import json
import logging
from pathlib import Path

import httpx

from server import config
from server.db import devices_store

log = logging.getLogger("fcm")

_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
_ENDPOINT = "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"

_creds = None          # cached google.oauth2 Credentials
_project_id: str | None = None


def _load() -> bool:
    """Lazily load the service-account credentials. Returns False if unavailable."""
    global _creds, _project_id
    if _creds is not None:
        return True
    path: Path = config.FCM_SERVICE_ACCOUNT
    if not path.exists():
        return False
    try:
        from google.oauth2 import service_account
        _creds = service_account.Credentials.from_service_account_file(
            str(path), scopes=[_SCOPE])
        _project_id = json.loads(path.read_text()).get("project_id")
        log.info("FCM enabled for project %s", _project_id)
        return True
    except Exception as exc:
        log.error("FCM key load failed: %s", exc)
        return False


def available() -> bool:
    return _load()


def _access_token() -> str:
    import google.auth.transport.requests
    if not _creds.valid:
        _creds.refresh(google.auth.transport.requests.Request())
    return _creds.token


def _send_sync(title: str, body: str, data: dict | None = None) -> int:
    """Send a notification to every registered device. Returns count delivered.
    Prunes tokens Firebase reports as unregistered/invalid. Blocking — call via
    push_all() from async code."""
    if not _load():
        return 0
    tokens = devices_store.all_tokens()
    if not tokens:
        return 0
    url = _ENDPOINT.format(project_id=_project_id)
    headers = {
        "Authorization": f"Bearer {_access_token()}",
        "Content-Type": "application/json",
    }
    sent = 0
    with httpx.Client(timeout=30) as c:
        for token in tokens:
            message: dict = {
                "token": token,
                "notification": {"title": title, "body": body},
                "android": {"priority": "high"},
            }
            if data:
                # FCM data values must be strings.
                message["data"] = {k: str(v) for k, v in data.items()}
            try:
                r = c.post(url, headers=headers, json={"message": message})
            except httpx.HTTPError as exc:
                log.warning("FCM send error: %s", exc)
                continue
            if r.status_code == 200:
                sent += 1
            elif r.status_code in (400, 404):
                # UNREGISTERED / invalid token — drop it so we stop retrying.
                err = ""
                try:
                    err = r.json().get("error", {}).get("status", "")
                except Exception:
                    pass
                if r.status_code == 404 or err in ("UNREGISTERED", "INVALID_ARGUMENT", "NOT_FOUND"):
                    devices_store.remove(token)
                    log.info("Pruned dead FCM token (%s)", err or r.status_code)
                else:
                    log.warning("FCM 400 for token: %s", r.text[:200])
            else:
                log.warning("FCM %s: %s", r.status_code, r.text[:200])
    return sent


async def push_all(title: str, body: str, data: dict | None = None) -> int:
    """Async wrapper — offloads the blocking sends to a worker thread."""
    return await asyncio.to_thread(_send_sync, title, body, data)
