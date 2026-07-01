"""
Background scheduler — the proactive half of the system.

Runs as an asyncio task started in main.py's lifespan. Every SCHEDULER_INTERVAL
seconds it:
  1. fires any reminders whose due_at has passed (Telegram push, then mark fired)
  2. checks battery; if low + unplugged + cooldown elapsed, pushes an alert

All pushes go out-of-band via server.notify (Telegram sendMessage).
"""

import asyncio
import logging
import time

import psutil

from server import config, notify, fcm
from server.db import reminders_store

log = logging.getLogger("scheduler")

_last_battery_alert: float = 0.0


async def _check_reminders() -> None:
    for r in reminders_store.due_now():
        text = f"⏰ REMINDER\n\n{r['text']}"
        if r.get("project"):
            text += f"\n\n(project: {r['project']})"
        ok = await notify.push_text(text, chat_id=r.get("chat_id"))
        # Also push to the Gajala app (no-op if FCM isn't configured).
        await fcm.push_all("⏰ Reminder", r["text"], data={"type": "reminder", "id": r["id"]})
        if ok:
            reminders_store.mark_fired(r["id"])
            log.info("Fired reminder #%s", r["id"])


async def _check_battery() -> None:
    global _last_battery_alert
    bat = psutil.sensors_battery()
    if bat is None:
        return
    now = time.time()
    if (bat.percent <= config.BATTERY_THRESHOLD
            and not bat.power_plugged
            and now - _last_battery_alert > config.BATTERY_ALERT_COOLDOWN):
        msg = (f"🔋 LOW BATTERY: {bat.percent:.0f}%\n"
               f"Your Mac is unplugged. Plug it in to keep Code-as-a-Chat alive.")
        await notify.push_text(msg)
        await fcm.push_all("🔋 Low battery", f"{bat.percent:.0f}% and unplugged", data={"type": "battery"})
        _last_battery_alert = now
        log.info("Sent low-battery alert at %.0f%%", bat.percent)


async def scheduler_loop() -> None:
    log.info("Scheduler started (interval=%ss, battery<%s%%)",
             config.SCHEDULER_INTERVAL, config.BATTERY_THRESHOLD)
    while True:
        try:
            await _check_reminders()
            if config.BATTERY_ALERTS:
                await _check_battery()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never let one bad tick kill the loop
            log.error("scheduler tick error: %s", exc)
        await asyncio.sleep(config.SCHEDULER_INTERVAL)
