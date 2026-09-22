"""Expose real provider availability without retaining prompts or credentials."""
import time

_failures: dict[str, dict] = {}


def failed(provider: str, error: Exception) -> None:
    text = str(error).lower()
    reason = ('authentication expired or rejected' if any(s in text for s in (
        'oauth', 'authenticate', 'authentication', '401', 'not logged in')) else
        'quota or rate limit reached' if any(s in text for s in (
            '429', 'quota', 'rate limit', 'usage limit')) else
        'provider unavailable or timed out')
    _failures[provider] = {'reason': reason, 'retry_at': time.time() + (
        300 if reason.startswith('authentication') else 60)}


def available(provider: str) -> bool:
    return _failures.get(provider, {}).get('retry_at', 0) <= time.time()


def succeeded(provider: str) -> None:
    _failures.pop(provider, None)


def snapshot() -> dict:
    return {p: {**v, 'cooling_down': not available(p)} for p, v in _failures.items()}
