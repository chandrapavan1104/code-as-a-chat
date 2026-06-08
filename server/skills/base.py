from abc import ABC, abstractmethod


class Skill(ABC):
    name: str
    description: str

    @abstractmethod
    async def run(self, prompt: str, **kwargs) -> str:
        ...
