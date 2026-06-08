from server.skills.base import Skill

registry: dict[str, Skill] = {}


def register(skill: Skill) -> None:
    registry[skill.name] = skill


def get_skill(name: str) -> Skill | None:
    return registry.get(name)
