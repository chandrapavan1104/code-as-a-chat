from server import config
from server.skills import get_skill, registry

# Explicit /command → skill name mapping
COMMAND_MAP: dict[str, str] = {
    "claude": "claude",
    "codex": "codex",
    "gemini": "antigravity",
    "status": "sysmon",
    "files": "filemanager",
    "shell": "shell",
    "sessions": "sessions",
    "projects": "projects",
    "memory": "memory",
    "forget": "memory",  # alias — handled as "clear" via the bot wrapper
    "ports": "ports",
    "firebase": "firebase",
    "usage": "usage",
    "notes": "notes",
    "context": "context",
    "reminders": "reminders",
    "remind": "reminders",   # alias
    "mac": "mac",
}


def init() -> None:
    """Import all skill modules so they self-register into the registry."""
    import server.skills.sysmon        # noqa: F401
    import server.skills.filemanager   # noqa: F401
    import server.skills.claude_code   # noqa: F401
    import server.skills.codex         # noqa: F401
    import server.skills.antigravity   # noqa: F401
    import server.skills.shell         # noqa: F401
    import server.skills.sessions      # noqa: F401
    import server.skills.projects      # noqa: F401
    import server.skills.memory        # noqa: F401
    import server.skills.ports         # noqa: F401
    import server.skills.firebase      # noqa: F401
    import server.skills.usage         # noqa: F401
    import server.skills.notes         # noqa: F401
    import server.skills.context       # noqa: F401
    import server.skills.reminders     # noqa: F401
    import server.skills.mac           # noqa: F401


async def route(command: str, prompt: str = "", **kwargs) -> str:
    skill_name = COMMAND_MAP.get(command, config.DEFAULT_SKILL)
    skill = get_skill(skill_name)
    if skill is None:
        available = ", ".join(f"/{c}" for c in COMMAND_MAP)
        return f"Unknown command: /{command}\nAvailable: {available}"
    return await skill.run(prompt, **kwargs)
