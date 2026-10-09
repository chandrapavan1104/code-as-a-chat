import pytest
from fastapi import HTTPException
from server.db import night_queue_store as queue, store
from server.work_results import deliver_pending_results
from server import api_v2


def _job(session="app:one", origin=None):
    return queue.add(project="/tmp", task="Research a subject", tag="mine",
                     session_id=session, origin_message_id=origin, request_id=f"request-{origin}")


def test_result_delivery_is_exact_reply_and_idempotent_across_retries():
    origin = store.append_turn("app:one", "user", "Research a subject")
    job = _job(origin=origin)
    queue.save_result(job, kind="report", text="A sourced report", completeness="complete")
    queue.update(job, status="completed")
    assert deliver_pending_results() == 1
    assert deliver_pending_results() == 0
    messages = store.get_recent("app:one", n=10)
    assert len(messages) == 2
    assert messages[-1]["reply_to_message_id"] == origin
    queue.update(job, status="closed")
    assert deliver_pending_results() == 0


def test_result_delivery_never_guesses_legacy_or_cross_session_origin():
    origin = store.append_turn("app:other", "user", "Private origin")
    for source in [None, origin]:
        job = _job(origin=source)
        queue.save_result(job, kind="report", text="Report")
        queue.update(job, status="completed")
    assert deliver_pending_results() == 0
    assert store.count("app:one") == 0


def test_api_returns_legacy_report_and_new_result_without_polluting_list():
    job = _job()
    queue.update(job, status="completed", summary="Old complete report")
    detail = api_v2.queue_result(job)
    assert detail["result"]["text"] == "Old complete report"
    assert detail["result"]["available"]
    assert "text" not in api_v2._job_view(queue.get(job))["result"]
    with pytest.raises(HTTPException) as exc:
        api_v2.queue_result(987654)
    assert exc.value.status_code == 404


def test_capture_rejects_origin_from_wrong_conversation(monkeypatch):
    origin = store.append_turn("app:other", "user", "Private origin")
    monkeypatch.setattr(api_v2, "_resolve_project_path", lambda value: "/tmp")
    with pytest.raises(HTTPException) as exc:
        api_v2.queue_add(api_v2.QueueJobIn(task="Research", session_id="app:one", origin_message_id=origin))
    assert exc.value.status_code == 400


def test_legacy_escalation_explains_repository_cause_without_false_exhaustion():
    from server.queue_supervisor import job_explanation
    job = {"id": 42, "status": "needs_you", "tag": "auto", "depends_on": [],
           "attempt_count": 2, "max_attempts": 3, "failure_kind": "worker_error",
           "summary": "not a git repo (fatal: not a git repository)",
           "blocker_reason": "Automatic recovery stopped after 2/3 attempts"}
    explanation = job_explanation(job)
    assert "repository" in explanation["blocker"]
    assert "2/3" not in explanation["blocker"]
    assert "Choose" in explanation["next_action"]
