"""Find every brokerage connector a Claude run could load, so none slips past the deny list.

`config.claude_deny_args()` used to name order tools under one hard-coded prefix
(`mcp__claude_ai_Robinhood__`). That prefix is derived from the connector's
display name, which differs by how it is attached (the desktop app showed the
same Robinhood connector under an id-based server name), and a second broker
would have different tool names altogether. A name that does not match is
simply not denied, so the list failed open.

Two sources are checked:
  • the Claude CLI's own view: the `mcp_servers` list in the first
    (`system/init`) message of a stream-json run. The probe is killed as soon
    as that line arrives, before any model request;
  • a project's `.mcp.json` in the run's working directory, which the global
    view cannot see.

The configured Robinhood prefix keeps its precise list (reads stay usable). Any
other server that looks like a brokerage is denied whole, because its order
tools cannot be known by name.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path

from server import config

log = logging.getLogger(__name__)

_BROKER_NAME = re.compile(
    r"robinhood|broker|trading|schwab|fidelity|e\W?trade|alpaca|webull|ibkr|"
    r"interactive.?brokers|tastytrade|tradier|vanguard|coinbase|kraken|binance|plaid",
    re.IGNORECASE)
PROBE_SECONDS = 30
REFRESH_SECONDS = 6 * 3600

_discovered: set[str] = set()    # whole-server deny entries (mcp__<server>)


def server_rule(name: str) -> str:
    """The permission-rule name of an MCP server, e.g. 'claude.ai Robinhood' ->
    'mcp__claude_ai_Robinhood' (the CLI replaces anything outside [A-Za-z0-9_-])."""
    return "mcp__" + re.sub(r"[^A-Za-z0-9_-]", "_", name)


def _is_broker(name: str, detail: str = "") -> bool:
    return bool(_BROKER_NAME.search(f"{name} {detail}"))


def _known(rule: str) -> bool:
    """The configured connector, whose order tools are denied one by one."""
    return config.BROKER_TOOL_PREFIX.rstrip("_") == rule


def servers_to_deny(names_with_detail: list[tuple[str, str]]) -> set[str]:
    return {server_rule(n) for n, d in names_with_detail
            if _is_broker(n, d) and not _known(server_rule(n))}


def project_servers(cwd: Path | str | None) -> set[str]:
    """Broker-looking servers declared in `<cwd>/.mcp.json`."""
    if not cwd:
        return set()
    try:
        data = json.loads((Path(cwd) / ".mcp.json").read_text(encoding="utf-8"))
        servers = data.get("mcpServers") or {}
    except (OSError, ValueError, AttributeError):
        return set()
    if not isinstance(servers, dict):
        return set()
    return servers_to_deny([(str(n), json.dumps(v)[:500]) for n, v in servers.items()])


def denied_servers(cwd: Path | str | None = None) -> list[str]:
    return sorted(_discovered | project_servers(cwd))


async def discover() -> set[str]:
    """Ask the Claude CLI which MCP servers a run would load. Keeps the previous
    answer when the probe fails, so a broken probe never shrinks the deny list."""
    global _discovered
    argv = ["claude", "-p", ".", "--tools", "", "--output-format", "stream-json",
            "--verbose", "--no-session-persistence"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=str(Path.home()), stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    except OSError as exc:
        log.warning("broker discovery could not start claude: %r", exc)
        return _discovered
    try:
        line = await asyncio.wait_for(proc.stdout.readline(), PROBE_SECONDS)
    except asyncio.TimeoutError:
        line = b""
    finally:
        try:
            proc.kill()                 # before the model request: nothing is sent
            await proc.wait()
        except ProcessLookupError:
            pass
    try:
        init = json.loads(line or b"{}")
    except ValueError:
        init = None
    if not isinstance(init, dict) or init.get("subtype") != "init":
        log.warning("broker discovery got no init message; keeping %s", sorted(_discovered))
        return _discovered
    servers = [(str(s.get("name") or ""), "") for s in init.get("mcp_servers") or []
               if isinstance(s, dict)]
    _discovered = servers_to_deny(servers)
    if _discovered:
        log.warning("brokerage connectors denied whole to coding runs: %s", sorted(_discovered))
    return _discovered


async def watch_loop() -> None:
    while True:
        try:
            await discover()
        except asyncio.CancelledError:
            raise
        except Exception as exc:       # discovery must never take the server down
            log.warning("broker discovery failed: %r", exc)
        await asyncio.sleep(REFRESH_SECONDS)
