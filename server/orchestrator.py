from server import config
from server.skills import discover, get_skill, command_map
from server.skills.base import SkillResult

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
    # Mobile sends typed slash commands inside a shell request. These are exact
    # owner commands, not suggestions for an LLM to reinterpret.
    if command in ("shell",config.DEFAULT_SKILL) and prompt.lstrip().startswith('/'):
        token,_,rest=prompt.lstrip().partition(' ')
        explicit=token[1:]
        if explicit not in COMMAND_MAP:
            return f"Unknown command: /{explicit}"
        command,prompt=explicit,rest.strip()
    skill_name = COMMAND_MAP.get(command, config.DEFAULT_SKILL)
    skill = get_skill(skill_name)
    if skill is None:
        available = ", ".join(f"/{c}" for c in sorted(COMMAND_MAP))
        return f"Unknown command: /{command}\nAvailable: {available}"
    # Check if the skill is enabled
    if not prefs.is_skill_enabled(skill_name):
        return f"Skill '{skill_name}' is currently disabled."
    # A directly-typed /projects switch is the user choosing, so it also moves
    # the default for new threads. The agent calling the same skill mid-turn
    # only rebinds that turn (see server/skills/projects.py).
    # The same holds for a directly-typed `/sessions continue <id>`.
    if skill_name in ("projects", "sessions"):
        kwargs.setdefault("persist", True)
    result = await skill.run(prompt, **kwargs)
    # Direct /run callers still receive the long-standing text contract. The
    # shell consumes SkillResult before this boundary and keeps its semantics.
    return result.message if isinstance(result, SkillResult) else str(result)
