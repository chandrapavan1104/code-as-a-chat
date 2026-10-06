"""Ask the owner's phone for something mid-turn and wait for the answer.

Gajala keeps no permanent connection to the phone (that would cost battery and
an always-on service). Instead, nearly every request already arrives over an
open /run/stream from the app, so the agent sends a `phone_request` frame down
that stream; the app performs it (with its own per-ability opt-in) and posts
the result to /api/phone/result/<id>. A turn with no live stream (Telegram,
Night Shift) simply cannot use the phone, and is told so.
"""

import asyncio
import uuid

_pending: dict[str, asyncio.Future] = {}


class PhoneUnavailable(Exception):
    pass


async def request(on_event, command: str, args: dict, *, timeout: float) -> dict:
    if on_event is None:
        raise PhoneUnavailable(
            "The phone can only be asked while you are talking to Gajala in the "
            "app; this request came from somewhere else.")
    rid = uuid.uuid4().hex
    future = asyncio.get_running_loop().create_future()
    _pending[rid] = future
    try:
        await on_event({"type": "phone_request", "id": rid,
                        "command": command, "args": args})
        return await asyncio.wait_for(future, timeout)
    except asyncio.TimeoutError:
        raise PhoneUnavailable(
            "The phone did not answer in time (app closed, or the request was "
            "not confirmed).") from None
    finally:
        _pending.pop(rid, None)


def deliver(rid: str, result: dict) -> bool:
    """Called by the API when the phone answers. False if nobody is waiting."""
    future = _pending.get(rid)
    if future is None or future.done():
        return False
    future.get_loop().call_soon_threadsafe(
        lambda: future.done() or future.set_result(result))
    return True
