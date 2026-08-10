from server import config
from server.skills import discover, get_skill, command_map

# Built once at init() from skill manifests: {telegram_command: skill_name}
COMMAND_MAP: dict[str, str] = {}


def init() -> None:
    """Auto-discover every skill, then build the command map from manifests."""
    global COMMAND_MAP
    config.ensure_home_base()   # guarantee the default workspace exists
    discover()
    COMMAND_MAP = command_map()


async def route(command: str, prompt: str = "", **kwargs) -> str:
    from server import prefs
    skill_name = COMMAND_MAP.get(command, config.DEFAULT_SKILL)
    skill = get_skill(skill_name)
    if skill is None:
        available = ", ".join(f"/{c}" for c in sorted(COMMAND_MAP))
        return f"Unknown command: /{command}\nAvailable: {available}"
    # Check if the skill is enabled
    if not prefs.is_skill_enabled(skill_name):
        return f"Skill '{skill_name}' is currently disabled."
    return await skill.run(prompt, **kwargs)
