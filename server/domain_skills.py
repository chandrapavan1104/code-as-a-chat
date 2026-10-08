"""Domain procedures are guidance; typed tools remain the execution boundary."""
from dataclasses import dataclass, asdict
import re

@dataclass(frozen=True)
class DomainSkill:
    name: str
    cues: tuple[str, ...]
    procedure: str
    acceptance: str
    tools: tuple[str, ...]
    version: int = 1

SKILLS = (
    DomainSkill('research', ('research', 'compare', 'providers', 'sources'),
        'Resolve the report/question and constraints. Refresh current evidence. Delegate long public research as a durable job.',
        'A sourced report is saved as a reply; accepted/running is not completed.',
        ('context.read_message', 'context.search', 'research')),
    DomainSkill('writing', ('rewrite', 'draft', 'post', 'wording', 'write'),
        'Retrieve the exact original and accepted edits. Preserve voice and scope. Avoid replacing an intact draft with a summary.',
        'Deliver the requested text applying all explicit corrections.',
        ('context.read_message', 'context.search', 'context.read_file')),
    DomainSkill('coding', ('code', 'repo', 'implement', 'deploy', 'project', 'bug'),
        'Resolve target project before execution. Inspect existing code/state; preserve unrelated work; use a capable coding executor.',
        'Requested behavior exists with appropriate checks; deployment claims require actual deployment evidence.',
        ('project.inspect', 'context.read_file', 'claude', 'codex', 'antigravity')),
    DomainSkill('music', ('music', 'songs', 'song', 'play', 'playlist', 'album'),
        'Resolve song/album/artist/language and aliases. Probe the selected phone player. Never substitute opened search for playback.',
        'Correct selection evidence, playing state and advancing position; otherwise report specific blocked/unverified stage.',
        ('phone',)),
    DomainSkill('calling', ('call', 'dial'),
        'Resolve contact and ambiguity. Require confirmation tied to target. Changing target invalidates confirmation.',
        'Phone reports the confirmed call placed; typing a number is incomplete.',
        ('phone',)),
    DomainSkill('reminders', ('remind', 'reminder', 'alarm', 'timer'),
        'Resolve time, timezone, recurrence, and subject. Check equivalent existing schedules before creating.',
        'A verified schedule record or actual on-device alarm with requested timing.',
        ('reminders', 'phone')),
)

def select(prompt):
    words = set(re.findall(r'\w+', prompt.lower()))
    return [s for s in SKILLS if words.intersection(s.cues)]


def guidance(prompt):
    selected = select(prompt)
    if not selected:
        return ''
    return '\n<domain_procedures>\nProvisional skills; revise after context retrieval.\n' + '\n'.join(
        f'{s.name}: {s.procedure} Success criteria: {s.acceptance}' for s in selected) + '\n</domain_procedures>'


def manifest():
    return [asdict(skill) for skill in SKILLS]
