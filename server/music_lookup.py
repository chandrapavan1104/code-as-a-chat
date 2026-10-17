"""Find the YouTube Music song a play request means, so the phone can open it.

Handing YouTube Music a search (MEDIA_PLAY_FROM_SEARCH, ACTION_SEARCH) only
lands on its results list: its play-from-search session is reserved for
Google's own clients, and nothing presses play. Opening the song itself —
`https://music.youtube.com/watch?v=<id>` in the app, exactly what a shared
song link does — starts playback immediately. This module turns "AR Rahman
songs" into that id using YouTube Music's public web search (the same request
music.youtube.com makes, no account or key), restricted to songs.

Best effort by design: any failure returns None and the phone falls back to
its search handoff.
"""

import logging
import re

import httpx

log = logging.getLogger(__name__)

_URL = "https://music.youtube.com/youtubei/v1/search?prettyPrint=false"
_CLIENT = {"clientName": "WEB_REMIX", "clientVersion": "1.20250101.01.00",
           "hl": "en", "gl": "IN"}
_SONGS_ONLY = "EgWKAQIIAWoQEAMQBBAJEAoQBRAREBAQFQ%3D%3D"
_HEADERS = {"User-Agent": "Mozilla/5.0", "Origin": "https://music.youtube.com",
            "Referer": "https://music.youtube.com/"}
# Nothing to look up: "play music", "play some songs" — leave those to the app.
_GENERIC = re.compile(r"^(?:(?:some|any|good|nice|the|a|my|play|me)\s+)*"
                      r"(?:songs?|music|tracks?)(?:\s+please)?$", re.I)
# Spoken nicknames YouTube Music's search does not resolve on its own.
_ALIASES = {"dsp": "Devi Sri Prasad", "arr": "A. R. Rahman", "spb": "S. P. Balasubrahmanyam",
            "mm keeravani": "M. M. Keeravani"}


def clean_query(query: str) -> str:
    """Search text for a spoken request; '' when it names nothing specific.

    "songs" is kept on purpose: "AR Rahman songs" finds his hits, while the
    bare name matched an unrelated track.
    """
    text = re.sub(r"\s+", " ", query).strip()
    if not text or _GENERIC.match(text):
        return ""
    text = re.sub(r"\s+by\s+", " ", text, flags=re.I)      # "X by Y" → "X Y"
    for short, full in _ALIASES.items():
        text = re.sub(rf"\b{re.escape(short)}\b", full, text, flags=re.I)
    return text


def _runs(column: dict) -> str:
    text = column.get("musicResponsiveListItemFlexColumnRenderer", {}).get("text", {})
    return "".join(run.get("text", "") for run in text.get("runs", []))


def _first_song(node) -> dict | None:
    """Depth-first: the first list row that carries a playable video id."""
    if isinstance(node, dict):
        row = node.get("musicResponsiveListItemRenderer")
        if isinstance(row, dict):
            video_id = (row.get("playlistItemData") or {}).get("videoId")
            columns = [_runs(c) for c in row.get("flexColumns", [])]
            if video_id and columns and columns[0]:
                byline = columns[1] if len(columns) > 1 else ""
                return {"video_id": video_id, "title": columns[0],
                        "artist": byline.split(" • ")[0].strip()}
        for value in node.values():
            hit = _first_song(value)
            if hit:
                return hit
    elif isinstance(node, list):
        for value in node:
            hit = _first_song(value)
            if hit:
                return hit
    return None


async def find_song(query: str, timeout: float = 6.0) -> dict | None:
    """Top song match → {video_id, title, artist}, or None."""
    q = clean_query(query)
    if not q:
        return None
    body = {"context": {"client": _CLIENT}, "query": q, "params": _SONGS_ONLY}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(_URL, json=body, headers=_HEADERS)
            response.raise_for_status()
            hit = _first_song(response.json())
    except (httpx.HTTPError, ValueError) as exc:
        log.info("YouTube Music lookup failed for %r: %s", q, exc)
        return None
    if hit and not re.fullmatch(r"[A-Za-z0-9_-]{11}", hit["video_id"]):
        return None
    return hit
