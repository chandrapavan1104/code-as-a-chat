"""YouTube Music lookup: query cleanup, response parsing and the device wiring."""

import asyncio

from server import music_lookup
from server.skills import device


def _row(video_id, title, byline):
    col = lambda t: {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [{"text": t}]}}}
    return {"musicResponsiveListItemRenderer": {
        "playlistItemData": {"videoId": video_id},
        "flexColumns": [col(title), col(byline)]}}


def test_clean_query():
    assert music_lookup.clean_query("AR Rahman songs") == "AR Rahman songs"
    assert music_lookup.clean_query("Kesariya by Arijit Singh") == "Kesariya Arijit Singh"
    assert music_lookup.clean_query("DSP songs") == "Devi Sri Prasad songs"
    for vague in ("music", "songs", "some songs", "play music", "good music please"):
        assert music_lookup.clean_query(vague) == ""


def test_first_song_skips_rows_without_video():
    data = {"contents": [
        {"musicResponsiveListItemRenderer": {"flexColumns": []}},
        _row("pNtMTsdLZJ8", "Jai Ho!", "A. R. Rahman • Slumdog Millionaire • 3:43"),
        _row("arwwqfap96M", "Manasaa", "A.R. Rahman • Yemaaya Chesave • 4:12"),
    ]}
    assert music_lookup._first_song(data) == {
        "video_id": "pNtMTsdLZJ8", "title": "Jai Ho!", "artist": "A. R. Rahman"}


def test_device_play_carries_the_song(monkeypatch):
    sent = {}

    async def fake_find(query):
        return {"video_id": "pNtMTsdLZJ8", "title": "Jai Ho!", "artist": "A. R. Rahman"}

    async def fake_request(channel, command, args, timeout):
        sent.update(command=command, args=args)
        return {"ok": True, "data": {"done": "Playing Jai Ho!."}}

    monkeypatch.setattr(music_lookup, "find_song", fake_find)
    monkeypatch.setattr(device.phone_bridge, "request", fake_request)
    result = asyncio.run(device.DeviceSkill().run("play AR Rahman songs", phone_stream=True,
                                                  on_event=lambda *_: None))
    assert result.status == "succeeded"
    assert sent["args"]["video_id"] == "pNtMTsdLZJ8"
    assert sent["args"]["query"] == "AR Rahman songs"


def test_spotify_is_not_looked_up(monkeypatch):
    async def boom(query):
        raise AssertionError("looked up")

    async def fake_request(channel, command, args, timeout):
        return {"ok": True, "data": {"done": "ok"}}

    monkeypatch.setattr(music_lookup, "find_song", boom)
    monkeypatch.setattr(device.phone_bridge, "request", fake_request)
    result = asyncio.run(device.DeviceSkill().run("play jazz on spotify", phone_stream=True,
                                                  on_event=lambda *_: None))
    assert result.status == "succeeded"
