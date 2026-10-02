"""Expose real provider availability without retaining prompts or credentials."""
import time

from server import usage_limits

_failures: dict[str, dict] = {}


def failed(provider: str, error: Exception) -> None:
    text = str(error).lower()
    reason = ('authentication expired or rejected' if any(s in text for s in (
        'oauth', 'authenticate', 'authentication', '401', 'not logged in')) else
        'quota or rate limit reached' if usage_limits.is_usage_limit(text) or 'quota' in text else
        'provider unavailable or timed out')
    _failures[provider] = {'reason': reason, 'retry_at': time.time() + (
        300 if reason.startswith('authentication') else 60),
        'notice': usage_limits.notice(provider.capitalize(), str(error))}


def available(provider: str) -> bool:
    return _failures.get(provider, {}).get('retry_at', 0) <= time.time()


def succeeded(provider: str) -> None:
    _failures.pop(provider, None)


def fallback_note() -> str:
    """Why a backup model answered, in words the owner can act on."""
    for provider, failure in _failures.items():
        if failure.get('notice'):
            return f"{failure['notice']} A backup model answered this."
    for provider, failure in _failures.items():
        return (f"{provider.capitalize()} was unavailable "
                f"({failure['reason']}); a backup model answered this.")
    return "The primary model was unavailable; a backup model answered this."


def snapshot() -> dict:
    return {p: {**v, 'cooling_down': not available(p)} for p, v in _failures.items()}
