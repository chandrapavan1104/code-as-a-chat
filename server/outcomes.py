"""Conservative completion states shared by every assistant exit path."""
import re

UNFINISHED = re.compile(
    r"could(?: not|n't)|cannot|can't|unable to|unverified|not verified|"
    r"still pending|remains? (?:unfinished|unverified)|missing (?:input|image|attachment)|"
    r"please (?:provide|upload)|need (?:your|the) (?:input|image|attachment)", re.I)
OBSERVED_CLAIM = re.compile(
    r"(?:is|are) (?:running|healthy|live|working|functioning)|"
    r"(?:deployed|committed|pushed|cloned|saved|created|deleted|scheduled)\b|"
    r"(?:current|latest) (?:status|usage|stats)", re.I)

_SWITCH_CLAIM = re.compile(
    r"\b(?:switched|moved|changed)\s+(?:you\s+)?(?:over\s+)?(?:in)?to\b([^.\n]{0,80})", re.I)
_SWITCH_NOUN = re.compile(r"\b(?:session|project|folder|directory|workspace|thread|repo)\b", re.I)


def false_switch_claim(reply: str, *, switched: bool, project_names: list[str],
                       steps: list[dict]) -> bool:
    """True when the reply says the chat moved to another project/session but
    nothing was rebound this turn. A fallback model once answered "Switched to
    the UltraSync session" after merely reading it; the next request then ran in
    the wrong folder."""
    if switched:
        return False
    # "Already on X" is a satisfied precondition, not a false claim.
    if any("already on" in str(s.get("result", "")).lower() for s in steps):
        return False
    for match in _SWITCH_CLAIM.finditer(reply or ""):
        target = match.group(1)
        low = target.lower()
        if _SWITCH_NOUN.search(target) or any(n.lower() in low for n in project_names if n):
            return True
    return False


def completion_status(reply: str, reason: str, steps: list[dict],
                      rejected: bool = False) -> str:
    if reason in {'llm_error', 'error', 'tool_failed'}:
        return 'failed'
    if re.search(r'please (?:provide|upload)|missing (?:input|image|attachment)', reply, re.I):
        return 'waiting_for_user'
    if rejected or reason in {'step_limit', 'duplicate_stop', 'no_action'} or UNFINISHED.search(reply):
        return 'unverified'
    if steps:
        meaningful = [s for s in steps if s.get('charged', True)]
        if meaningful and meaningful[-1].get('status') in {
                'failed', 'unsupported', 'needs_permission', 'not_found'}:
            return 'failed'
        # Legacy strings are observations, not typed confirmation of mutation.
        if OBSERVED_CLAIM.search(reply) and not (
                meaningful and all(s.get('status') == 'succeeded' for s in meaningful)):
            return 'unverified'
    elif OBSERVED_CLAIM.search(reply):
        return 'unverified'
    return 'completed'
