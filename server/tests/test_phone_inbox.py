import asyncio
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from server import config, media, phone_inbox
from server.main import require_token

TRANSFER = "1" * 32
AUTH = {"X-API-Token": config.API_TOKEN}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(media, "UPLOADS_DIR", tmp_path / "uploads")
    app = FastAPI()
    app.include_router(phone_inbox.router, prefix="/api", dependencies=[Depends(require_token)])
    return TestClient(app)


def test_streaming_file_is_private_and_has_one_idempotent_receipt(client):
    path = f"/api/phone/inbox/{TRANSFER}"
    args = {"params": {"name": "Report [draft].md"}, "content": b"# Report"}
    assert client.put(path, **args).status_code == 401
    first = client.put(path, headers=AUTH, **args)
    assert first.status_code == 200
    receipt = first.json()
    assert Path(receipt["path"]).read_bytes() == b"# Report"
    assert receipt["size"] == 8 and receipt["name"] == "Report [draft].md"
    assert client.put(path, headers=AUTH, **args).json() == receipt
    assert client.get("/api/phone/inbox", headers=AUTH).json() == {"files": [receipt]}
    attach = client.post(f"{path}/attachment", headers=AUTH).json()
    assert "]" not in attach["path"]
    assert media.is_served_path(Path(attach["path"]))
    assert Path(attach["path"]).read_bytes() == b"# Report"
    assert client.put(path, headers=AUTH, params={"name": "other"}, content=b"a").status_code == 409


def test_validation_and_interruption_leave_no_visible_file(client, monkeypatch):
    path = f"/api/phone/inbox/{TRANSFER}"
    monkeypatch.setattr(phone_inbox, "MAX_BYTES", 4)
    assert client.put(path, headers=AUTH, params={"name": "../outside"},
                      content=b"a").status_code == 400
    assert client.put(path, headers=AUTH, params={"name": "big.zip"},
                      content=b"large").status_code == 413

    async def retry_after_disconnect():
        messages = iter([
            {"type": "http.request", "body": b"a", "more_body": True},
            {"type": "http.disconnect"},
        ])

        async def receive():
            return next(messages)

        with pytest.raises(HTTPException, match="disconnected"):
            await phone_inbox.receive(TRANSFER, "resume.txt", Request({
                "type": "http", "headers": [],
            }, receive))

    asyncio.run(retry_after_disconnect())
    assert client.get("/api/phone/inbox", headers=AUTH).json() == {"files": []}
    assert client.put(path, headers=AUTH, params={"name": "resume.txt"},
                      content=b"ok").status_code == 200
