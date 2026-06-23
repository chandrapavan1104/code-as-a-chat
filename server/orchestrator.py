from server import config
from server.skills import discover, get_skill, command_map

# Built once at init() from skill manifests: {telegram_command: skill_name}
COMMAND_MAP: dict[str, str] = {}


def init() -> None:
    """Auto-discover every skill, then build the command map from manifests."""
    global COMMAND_MAP
    discover()
    COMMAND_MAP = command_map()


async def route(command: str, prompt: str = "", **kwargs) -> str:
    skill_name = COMMAND_MAP.get(command, config.DEFAULT_SKILL)
    skill = get_skill(skill_name)
    if skill is None:
        available = ", ".join(f"/{c}" for c in sorted(COMMAND_MAP))
        return f"Unknown command: /{command}\nAvailable: {available}"
    return await skill.run(prompt, **kwargs)
