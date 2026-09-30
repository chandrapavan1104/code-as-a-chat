from fastapi.testclient import TestClient

from server import config, main


def _client():
    return TestClient(main.app), {"X-API-Token": config.API_TOKEN}


def test_voice_model_absent_reports_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VOICE_MODEL_PATH", tmp_path / "missing.litertlm")
    client, headers = _client()
    info = client.get("/api/voice/model", headers=headers).json()
    assert info["available"] is False
    assert client.get("/api/voice/model/file", headers=headers).status_code == 404


def test_voice_model_served_with_size(tmp_path, monkeypatch):
    model = tmp_path / "Qwen3-0.6B.litertlm"
    model.write_bytes(b"weights" * 100)
    monkeypatch.setattr(config, "VOICE_MODEL_PATH", model)
    client, headers = _client()
    info = client.get("/api/voice/model", headers=headers).json()
    assert info == {"available": True, "name": model.name, "size": 700,
                    "model_type": "qwen3", "file_type": "litertlm"}
    body = client.get("/api/voice/model/file", headers=headers)
    assert body.status_code == 200 and body.content == model.read_bytes()


def test_voice_model_requires_token(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VOICE_MODEL_PATH", tmp_path / "m.litertlm")
    client, _ = _client()
    assert client.get("/api/voice/model/file").status_code == 401
