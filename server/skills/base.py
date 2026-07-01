from abc import ABC, abstractmethod


class Skill(ABC):
    """Base class for all skills.

    A skill is self-describing: the metadata below is the single source of
    truth that auto-wires the orchestrator command map, the shell agent's
    tool catalog, and the Telegram bot's commands. Adding a skill = drop one
    file in server/skills/ — no other file needs editing.

    Required:
      name         registry key + default command (e.g. "notes")
      description  one-liner shown in /skills and the bot menu

    Optional (sensible defaults):
      command          Telegram /command, defaults to `name`
      aliases          extra /commands routed to this skill (e.g. ["remind"])
      help_line        one-line menu entry; falls back to description
      agent_doc        rich block the shell agent reads to decide routing;
                       falls back to description. Keep it tight — every word
                       here is sent to the router LLM on every free-text turn.
      expose_to_agent  if False, only reachable via explicit /command
      final_output     output is already presentation-ready; the shell agent
                       may return it without a second persona/format LLM call
      passthrough      reply is a distinct persona / verbatim content; the
                       shell returns it untouched (e.g. the diary's "Anna")
    """

    name: str
    description: str

    # ── manifest (optional; defaults keep older skills working) ──────────────
    command: str | None = None
    aliases: list[str] = []
    help_line: str | None = None
    agent_doc: str = ""
    expose_to_agent: bool = True
    final_output: bool = False
    passthrough: bool = False

    @abstractmethod
    async def run(self, prompt: str, **kwargs) -> str:
        ...

    # ── derived accessors ────────────────────────────────────────────────────

    @property
    def primary_command(self) -> str:
        return self.command or self.name

    @property
    def menu_line(self) -> str:
        return self.help_line or self.description

    @property
    def routing_doc(self) -> str:
        return (self.agent_doc or self.description).strip()
