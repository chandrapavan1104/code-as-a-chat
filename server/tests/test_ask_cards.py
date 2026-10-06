import asyncio
import json

from fastapi.testclient import TestClient

from server import ask_cards, config, main, workspace
from server.skills import shell

CARD = '[[ask:{"question":"Which build?","options":["Debug","Release"],"multi":false}]]'


def test_valid_card_is_kept_once_at_the_end():
    text = ask_cards.normalize(f"Ready to build.\n{CARD}\nAnything else?")
    assert text.startswith("Ready to build.\n\nAnything else?")
    assert text.endswith('[[ask:{"question": "Which build?", "options": ["Debug", "Release"], "multi": false}]]')


def test_malformed_or_degenerate_cards_are_dropped():
    for bad in ('[[ask:{"question":"Pick","options":["only one"]}]]',
                '[[ask:{"question":"","options":["a","b"]}]]',
                '[[ask:{not json}]]',
                '[[ask:{"question":"Pick","options":["a","a"]}]]'):
        assert ask_cards.normalize("Hi " + bad) == "Hi"


def test_plain_text_rendering_for_telegram():
    assert ask_cards.as_plain_text("Ready.\n\n" + CARD) == (
        "Ready.\n\nWhich build?\n1. Debug\n2. Release")
    multi = '[[ask:{"question":"Ship which?","options":["app","server"],"multi":true}]]'
    assert "Ship which? (pick any)" in ask_cards.as_plain_text(multi)


def test_shell_reply_card_is_normalized(monkeypatch, tmp_path):
    decision = json.dumps({"action": "done",
                           "reply": f"Two options. {CARD} [[ask:{{broken}}]]"})

    async def fake_haiku(*args, **kwargs):
        return decision
    monkeypatch.setattr(shell, "_haiku", fake_haiku)

    async def turn():
        with workspace.bound(tmp_path):
            return await shell.ShellSkill().run("which variant should we use?")
    reply = asyncio.run(turn())
    assert reply.count("[[ask:") == 1 and "{broken}" not in reply
    assert reply.startswith("Two options.")


def test_run_endpoint_gives_plain_text(monkeypatch):
    async def route(command, prompt, **kwargs):
        return "Ready.\n\n" + CARD
    monkeypatch.setattr(main.orchestrator, "route", route)
    r = TestClient(main.app).post("/run", json={"command": "mac", "prompt": "x"},
                                  headers={"X-API-Token": config.API_TOKEN})
    assert r.json()["result"] == "Ready.\n\nWhich build?\n1. Debug\n2. Release"
