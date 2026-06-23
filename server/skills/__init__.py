import importlib
import logging
import pkgutil

from server.skills.base import Skill

log = logging.getLogger("skills")

registry: dict[str, Skill] = {}

# Modules in this package that are NOT skills (no auto-import as skills).
_NON_SKILL_MODULES = {"base", "cli_base"}


def register(skill: Skill) -> None:
    registry[skill.name] = skill


def get_skill(name: str) -> Skill | None:
    return registry.get(name)


def discover() -> None:
    """Import every skill module in this package so each self-registers.
    Replaces the old hand-maintained import list."""
    import server.skills as pkg
    for mod in pkgutil.iter_modules(pkg.__path__):
        if mod.name in _NON_SKILL_MODULES or mod.name.startswith("_"):
            continue
        try:
            importlib.import_module(f"server.skills.{mod.name}")
        except Exception as exc:  # one bad skill shouldn't sink the rest
            log.error("skill %s failed to import: %s", mod.name, exc)


def command_map() -> dict[str, str]:
    """Build {telegram_command: skill_name} from every skill's manifest."""
    cmap: dict[str, str] = {}
    for name, skill in registry.items():
        cmap[skill.primary_command] = name
        for alias in getattr(skill, "aliases", []) or []:
            cmap[alias] = name
    return cmap


def manifest() -> list[dict]:
    """Serializable description of all skills — served at /skills, consumed by
    the bot (and later the Android client) to self-configure."""
    out = []
    for name, skill in registry.items():
        out.append({
            "name": name,
            "command": skill.primary_command,
            "aliases": list(getattr(skill, "aliases", []) or []),
            "description": skill.description,
            "help_line": skill.menu_line,
            "expose_to_agent": getattr(skill, "expose_to_agent", True),
            "final_output": getattr(skill, "final_output", False),
            "passthrough": getattr(skill, "passthrough", False),
        })
    return sorted(out, key=lambda d: d["name"])
