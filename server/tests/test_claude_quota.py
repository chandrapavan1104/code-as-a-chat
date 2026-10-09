import json
from datetime import datetime, timezone
from pathlib import Path
from server import claude_quota
from server.claude_quota import refresh as real_refresh


def test_native_screen_requires_both_windows_and_accepts_zero():
    text = 'Current session\n 0% used\nResets 2:20pm\nCurrent week (all models)\n 5% used'
    assert claude_quota.parse_usage_screen(text) == {
        'five_hour': {'used_percentage': 0}, 'seven_day': {'used_percentage': 5}}
    assert claude_quota.parse_usage_screen('Current session 0% used') is None
    assert claude_quota.parse_usage_screen(text.replace('5%', '105%')) is None


def test_snapshot_expires_and_does_not_become_zero(tmp_path, monkeypatch):
    file = tmp_path / 'quota.json'
    monkeypatch.setattr(claude_quota, 'capture_path', lambda: file)
    file.write_text(json.dumps({'capturedAt': '2026-01-01T00:00:00Z', 'rate_limits': {}}))
    assert not claude_quota.snapshot()[2]
    assert claude_quota.metadata()['quota_stale']
    file.write_text('invalid')
    assert claude_quota.snapshot() == ({}, None, False)


def test_refresh_coalesces_failure_and_never_spams_cli(monkeypatch):
    monkeypatch.setattr(claude_quota, 'snapshot', lambda: ({}, None, False))
    monkeypatch.setattr(claude_quota, '_last_attempt', -1000)
    monkeypatch.setattr(claude_quota.time, 'monotonic', lambda: 1000)
    calls = []
    monkeypatch.setattr(claude_quota, '_native_refresh', lambda cwd: calls.append(cwd) or 'Unavailable')
    assert real_refresh('/tmp/no-project') == 'Unavailable'
    assert real_refresh('/tmp/no-project') == 'Unavailable'
    assert len(calls) == 1


def test_capture_contains_only_quota_and_timestamp(tmp_path, monkeypatch):
    path = tmp_path / 'capture.json'
    monkeypatch.setattr(claude_quota, 'capture_path', lambda: path)
    claude_quota.save_capture({'five_hour': {'used_percentage': 0}, 'seven_day': {'used_percentage': 5}})
    data = json.loads(path.read_text())
    assert set(data) == {'capturedAt', 'source', 'rate_limits'}
    assert claude_quota.snapshot()[2]


def test_iso_reset_expiry_is_respected(monkeypatch):
    from server import api_v2
    monkeypatch.setattr(api_v2.time, 'time', lambda: 100)
    assert not api_v2._limit_is_current({'resetsAt': '1970-01-01T00:00:50Z'})
    assert api_v2._limit_is_current({'resetsAt': '1970-01-01T00:02:00Z'})
    assert not api_v2._limit_is_current({'resetsAt': 'invalid'})
