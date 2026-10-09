import asyncio
import pytest


def _store(tmp_path, monkeypatch):
    from server.db import night_queue_store
    monkeypatch.setattr(night_queue_store, "DB_PATH", tmp_path / "queue.db")
    return night_queue_store


def test_attempt_logs_are_bounded_and_secret_redacted(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    job_id = store.add(project="/tmp/repo", task="build feature")
    attempt = store.begin_attempt(job_id, "codex", stage="agent_run")
    store.finish_attempt(
        attempt["id"], status="failed", stage="agent_run",
        stdout='Authorization: Bearer abcdefghijklmnopqrstuvwxyz\n'
               '{"api_key": "top-secret"} token=another-secret\n'
               "-----BEGIN PRIVATE KEY-----secret-key-----END PRIVATE KEY-----\n",
        output="x" * 40000,
    )
    row = store.attempts(job_id, include_logs=True)[0]
    assert "abcdefghijklmnopqrstuvwxyz" not in row["stdout"]
    assert "top-secret" not in row["stdout"]
    assert "another-secret" not in row["stdout"]
    assert "secret-key" not in row["stdout"]
    assert "[REDACTED]" in row["stdout"]
    assert len(row["output"]) == store._ATTEMPT_OUTPUT_LIMIT
    assert row["output_truncated"] == 1
    assert row["status"] == "failed"


def test_results_keep_complete_report_and_authenticated_download(tmp_path, monkeypatch):
    from server import media
    store = _store(tmp_path, monkeypatch)
    uploads = tmp_path / "uploads"
    monkeypatch.setattr(media, "UPLOADS_DIR", uploads)
    job_id = store.add(project="/tmp/repo", task="research report")
    saved = store.save_result(job_id, kind="research", text="Full sourced report\nhttps://example.org",
                              artifacts=[{"kind": "research_report", "ref": f"queue:{job_id}:result"}])
    result = store.result(job_id)
    assert saved["available"]
    assert result["text"].endswith("https://example.org")
    report_file = next((uploads / "queue" / str(job_id)).glob("result-*.md"))
    assert report_file.read_text() == result["text"]
    assert any(a.get("path") == str(report_file) for a in result["artifacts"])


def test_timeout_preserves_partial_subprocess_output(tmp_path, monkeypatch):
    import sys
    from server import night_exec

    monkeypatch.setattr(night_exec, "_argv", lambda *_: [
        sys.executable, "-c", "import time; print('partial report', flush=True); time.sleep(5)"])
    monkeypatch.setattr(night_exec, "_parse", lambda _engine, stdout, _stderr: (stdout.strip(), 0, 0))
    captured = {}
    text, _, _, error = asyncio.run(night_exec.run_job(
        "codex", str(tmp_path), "slow task", timeout=0.2,
        on_result=lambda **data: captured.update(data)))
    assert error and "limit" in error
    assert "partial report" in text
    assert "partial report" in captured["stdout"]


def test_request_id_is_idempotent_and_rejects_mismatched_replay(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    args = dict(project="/tmp/repo", task="write tests", session_id="app:install:chat:1",
                origin_message_id=44, request_id="capture-1")
    first = store.add(**args)
    assert store.add(**args) == first
    with pytest.raises(ValueError, match="different queue task"):
        store.add(**{**args, "task": "different request"})
    assert len(store.list_jobs(limit=10)) == 1


def test_result_receipts_are_versioned_and_not_lost_after_many_jobs(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    linked = store.add(project="/tmp/repo", task="report", session_id="app:install:chat:1",
                       origin_message_id=44)
    store.update(linked, status="completed")
    store.save_result(linked, kind="research", text="first")
    first_saved = store.get(linked)["result_saved_at"]
    assert store.result_receipt_candidates(limit=1)[0]["id"] == linked
    store.mark_result_delivered(linked, first_saved)
    assert store.result_receipt_candidates() == []
    store.save_result(linked, kind="research", text="revised")
    assert store.result_receipt_candidates()[0]["result_text"] == "revised"


def test_legacy_failed_summary_is_partial_not_complete(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    job_id = store.add(project="/tmp/repo", task="old failure")
    store.update(job_id, status="failed", summary="worker error")
    assert store.result(job_id)["completeness"] == "partial"
    assert store.result_metadata(store.get(job_id))["completeness"] == "partial"


def test_failed_mine_explanation_does_not_show_stale_running_action(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    job_id = store.add(project="/tmp/repo", task="build feature", tag="mine")
    store.update(job_id, status="failed", summary="not a git repo",
                 failure_kind="invalid_repo", next_action="A worker is implementing this task.")
    from server.queue_supervisor import job_explanation
    explanation = job_explanation(store.get(job_id))
    assert "not a Git repository" in explanation["blocker"]
    assert "Choose a Git repository" in explanation["next_action"]
    assert "implementing" not in explanation["next_action"]


def test_invalid_repo_is_classified_before_worker_launch(tmp_path, monkeypatch):
    from server import night_shift
    store = _store(tmp_path, monkeypatch)
    job_id = store.add(project=str(tmp_path), task="make a code change", tag="mine")
    launched = []

    async def forbidden_runner(*args, **kwargs):
        launched.append(True)
        return "", 0, 0, None

    async def no_notify(*args, **kwargs):
        return None

    monkeypatch.setattr(night_shift.night_exec, "run_job", forbidden_runner)
    monkeypatch.setattr(night_shift, "_notify_status", no_notify)
    asyncio.run(night_shift._process_job_locked(
        store.get(job_id), "codex", str(tmp_path), job_id))
    job = store.get(job_id)
    attempt = store.attempts(job_id)[0]
    assert not launched
    assert job["status"] == "failed"
    assert job["failure_kind"] == "invalid_repo"
    assert attempt["stage"] == "repository_preflight"
    assert attempt["status"] == "failed"
