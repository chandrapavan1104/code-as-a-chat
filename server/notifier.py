"""One place that both LOGS a notification to the inbox and PUSHES it.

Feature code should call `notify_app(...)` rather than touching `fcm` or
`notifications_store` directly, so the app's Alerts tab and the FCM push can never
drift apart: the same event always produces exactly one durable inbox row and one
push carrying that row's id (so a tap opens the exact item).
"""

import logging

from server import fcm
from server.db import notifications_store

log = logging.getLogger("notifier")

_PREVIEW_CHARS = 160


def _preview(text: str, limit: int = _PREVIEW_CHARS) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


async def notify_app(type: str, title: str, body: str = "", *,
                     data: dict | None = None, needs_response: bool = False,
                     ref_kind: str | None = None, ref_id: int | None = None,
                     telegram: bool = False) -> int:
    """Record an inbox notification and push it. Returns the inbox row id.

    Best-effort on the transport side: a failed push (or no FCM key) never loses
    the inbox row — the app still sees it on next fetch.
    """
    notif_id = notifications_store.add(
        type=type, title=title, body=body, data=data,
        needs_response=needs_response, ref_kind=ref_kind, ref_id=ref_id)

    push_data = {"type": type, "notif_id": notif_id}
    if ref_id is not None:
        push_data["ref_id"] = ref_id
    if data:
        push_data.update({k: v for k, v in data.items() if k not in push_data})

    try:
        if fcm.available():
            await fcm.push_all(title, _preview(body or title), data=push_data)
    except Exception as exc:  # a push failure must not lose the inbox row
        log.warning("notify_app push failed (%s): %s", type, exc)

    if telegram:
        try:
            from server import notify
            await notify.push_text(f"{title}\n\n{body}".strip())
        except Exception:
            pass

    return notif_id
