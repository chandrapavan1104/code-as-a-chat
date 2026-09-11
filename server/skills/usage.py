"""
usage skill — wraps the user's `codaur` CLI (coding-agent usage report).

Source repo: ~/Projects/Codaur
Binary:      /opt/homebrew/bin/codaur  (installed via `npm link`)

What it surfaces:
  • Codex   — real rate-limit % from local JSONL snapshots (5h + weekly windows)
  • Claude  — token totals; computed % of a daily budget (USAGE_BUDGET_CLAUDE)
  • Gemini  — token totals; computed % of a daily budget (USAGE_BUDGET_GEMINI)
  • Antigrav — conversation count only (no local token data)

Subcommands (passed via prompt):
  (empty) | check          mobile summary across all providers
  <provider>               just that provider (codex|claude|gemini|antigravity)
  today                    only today's numbers across providers
  days <n>                 change recent-day window (default 30)
  threads [provider]       most recent threads + which project they hit
  raw                      dump the underlying JSON (debug)
"""

import asyncio
import datetime as dt
import json
import shutil
from pathlib import Path
from server.skills.base import Skill
from server.skills import register
from server import config


CMD_TIMEOUT = 60
CLI_BIN = "codaur"


# ── subprocess helper ─────────────────────────────────────────────────────────

async def _run(args: list[str], timeout: int = CMD_TIMEOUT) -> tuple[int, str, str]:
    if shutil.which(CLI_BIN) is None:
        return (127, "",
                f"{CLI_BIN} not found.\n"
                "Install: cd ~/Projects/Codaur && npm link")
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            CLI_BIN, *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        if proc is not None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        return (-1, "", f"timed out after {timeout}s")
    return (proc.returncode,
            out_b.decode(errors="replace"),
            err_b.decode(errors="replace"))


async def _fetch(provider: str = "all", today: bool = False,
                 days: int | None = None) -> dict | None:
    args = ["--provider", provider, "--json"]
    if today:
        args.append("--today")
    if days is not None:
        args += ["--days", str(days)]
    rc, out, err = await _run(args)
    if rc != 0 and not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


# ── formatting helpers ────────────────────────────────────────────────────────

def _bar(percent: float, width: int = 12) -> str:
    filled = max(0, min(width, int(round((percent / 100.0) * width))))
    return "[" + "█" * filled + "·" * (width - filled) + "]"


def _humanize_tokens(n: int) -> str:
    if n is None:
        return "?"
    if n >= 1_000_000_000:
        return f"{n / 1e9:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1e6:.1f}M"
    if n >= 1_000:
        return f"{n / 1e3:.1f}K"
    return str(n)


def _humanize_until(target_unix: int | None, ref_iso: str | None = None) -> str:
    if not target_unix:
        return ""
    if ref_iso:
        try:
            ref = dt.datetime.fromisoformat(ref_iso.replace("Z", "+00:00"))
        except ValueError:
            ref = dt.datetime.now(dt.timezone.utc)
    else:
        ref = dt.datetime.now(dt.timezone.utc)
    target = dt.datetime.fromtimestamp(target_unix, tz=dt.timezone.utc)
    secs = int((target - ref).total_seconds())
    if secs <= 0:
        return "expired"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h {(secs % 3600) // 60}m"
    days = secs // 86400
    return f"{days}d {(secs % 86400) // 3600}h"


def _billable(usage: dict | None) -> int:
    """Tokens that meaningfully count toward subscription quota.
    Excludes cache reads (which Anthropic discounts heavily / Google doesn't bill for reads)."""
    if not usage:
        return 0
    return ((usage.get("inputTokens") or 0)
            + (usage.get("outputTokens") or 0)
            + (usage.get("cacheCreationTokens") or 0)
            + (usage.get("reasoningTokens") or 0))


# ── per-provider blocks ───────────────────────────────────────────────────────

def _block_codex(rep: dict) -> list[str]:
    out = ["CODEX"]
    snap = rep.get("latestRateLimitSnapshot")
    if snap:
        plan = snap.get("planType") or "?"
        out[-1] = f"CODEX ({plan})"
        gen_at = rep.get("generatedAt")
        primary = snap.get("primary") or {}
        secondary = snap.get("secondary") or {}
        if primary:
            pct = primary.get("used_percent", 0)
            reset_in = _humanize_until(primary.get("resets_at"), gen_at)
            tail = f"  resets in {reset_in}" if reset_in else ""
            out.append(f"  5h:    {pct:>3}%  {_bar(pct)}{tail}")
        if secondary:
            pct = secondary.get("used_percent", 0)
            reset_in = _humanize_until(secondary.get("resets_at"), gen_at)
            tail = f"  resets in {reset_in}" if reset_in else ""
            out.append(f"  week:  {pct:>3}%  {_bar(pct)}{tail}")
    totals = rep.get("totals") or {}
    today_tok = totals.get("todayTokens") or 0
    total_tok = totals.get("tokens") or 0
    threads = totals.get("threads") or 0
    out.append(f"  today: {_humanize_tokens(today_tok)} tokens")
    out.append(f"  30d:   {_humanize_tokens(total_tok)} tokens, {threads} threads")
    return out


def _block_budgeted(rep: dict, label: str, budget: int) -> list[str]:
    """Generic block for Claude/Gemini — token counts + computed % vs budget."""
    out = [label.upper()]
    totals = rep.get("totals") or {}
    today_bill = _billable(totals.get("todayUsage"))
    today_tot = totals.get("todayTokens") or 0
    all_bill = _billable(totals.get("usage"))
    all_tot = totals.get("tokens") or 0
    threads = totals.get("threads") or 0

    pct = (today_bill / budget * 100.0) if budget else 0
    pct = min(pct, 999)
    out.append(f"  today: {pct:>3.0f}%  {_bar(min(pct, 100))}  ({_humanize_tokens(today_bill)} of {_humanize_tokens(budget)})")
    out.append(f"  today total (incl cache): {_humanize_tokens(today_tot)} tokens")
    out.append(f"  30d:   {_humanize_tokens(all_bill)} billable, "
               f"{_humanize_tokens(all_tot)} total, {threads} threads")
    return out


def _block_antigravity(rep: dict) -> list[str]:
    out = ["ANTIGRAVITY"]
    totals = rep.get("totals") or {}
    threads = totals.get("threads") or 0
    events = totals.get("events") or 0
    out.append(f"  {threads} conversations, {events} events (tokens not exposed locally)")
    return out


# ── views ─────────────────────────────────────────────────────────────────────

def _format_header(payload: dict) -> str:
    gen = payload.get("generatedAt", "")
    f = payload.get("filters", {}) or {}
    window = "today" if f.get("today") else f"{f.get('days', 30)}d"
    when = gen[:16].replace("T", " ") if gen else "?"
    return f"LLM USAGE  ({when} UTC, window={window})"


def _format_all(payload: dict) -> str:
    lines = [_format_header(payload), ""]
    reports = payload.get("reports") or []
    by_provider = {r.get("provider"): r for r in reports}

    if "codex" in by_provider:
        lines += _block_codex(by_provider["codex"])
        lines.append("")
    if "claude" in by_provider:
        lines += _block_budgeted(by_provider["claude"], "claude", config.USAGE_BUDGET_CLAUDE)
        lines.append("")
    if "gemini" in by_provider:
        lines += _block_budgeted(by_provider["gemini"], "gemini", config.USAGE_BUDGET_GEMINI)
        lines.append("")
    if "antigravity" in by_provider:
        lines += _block_antigravity(by_provider["antigravity"])
        lines.append("")

    lines.append("Tip: /usage threads — recent chats per project")
    return "\n".join(lines).rstrip()


def _format_single(rep: dict) -> str:
    provider = rep.get("provider", "?")
    lines = []
    if provider == "codex":
        lines = _block_codex(rep)
    elif provider == "claude":
        lines = _block_budgeted(rep, "claude", config.USAGE_BUDGET_CLAUDE)
    elif provider == "gemini":
        lines = _block_budgeted(rep, "gemini", config.USAGE_BUDGET_GEMINI)
    elif provider == "antigravity":
        lines = _block_antigravity(rep)
    else:
        return json.dumps(rep, indent=2)[:1500]

    # Add recent-day breakdown
    daily = rep.get("recentDailyTotals") or []
    if daily:
        lines.append("")
        lines.append("RECENT DAYS:")
        for d in daily[:10]:
            day = d.get("day", "?")
            tok = d.get("tokens") or 0
            th = d.get("threads") or 0
            lines.append(f"  {day}  {_humanize_tokens(tok):>7}  ({th} threads)")
    return "\n".join(lines)


def _format_threads(rep: dict | None, all_payload: dict | None = None, limit: int = 8) -> str:
    """Show most-recently-active threads with the project path each was in."""
    threads_by_provider: dict[str, list] = {}
    if rep is not None:
        threads_by_provider[rep["provider"]] = rep.get("recentThreads") or []
    elif all_payload is not None:
        for r in all_payload.get("reports", []):
            threads_by_provider[r.get("provider")] = r.get("recentThreads") or []

    lines = ["RECENT THREADS:"]
    home_prefix = str(Path.home())   # username-agnostic (works for any user)
    for prov, threads in threads_by_provider.items():
        if not threads:
            continue
        lines.append("")
        lines.append(f"-- {prov.upper()} --")
        for t in threads[:limit]:
            updated = (t.get("updated") or "")[:16]
            tokens = _humanize_tokens(t.get("tokens") or 0)
            cwd = t.get("cwd", "") or t.get("project", "") or ""
            if cwd.startswith(home_prefix):
                cwd = "~" + cwd[len(home_prefix):]
            cwd = cwd or "(no path)"
            lines.append(f"  {updated}  {tokens:>6}  {cwd}")
    if len(lines) == 1:
        lines.append("(no recent threads found)")
    return "\n".join(lines)


# ── skill ─────────────────────────────────────────────────────────────────────

PROVIDERS = {"codex", "claude", "gemini", "antigravity"}


class UsageSkill(Skill):
    name = "usage"
    description = ("LLM provider usage: /usage | <provider> | today | "
                   "days <n> | threads | raw")
    final_output = True
    agent_doc = ("Local LLM usage report (Codex/Claude/Gemini/Antigravity). Reads on-disk "
                 "session files. Shows real rate-limit % for Codex; computed % vs daily token "
                 'budget for Claude/Gemini. args: "" | "<provider>" | "today" | "days <n>" | '
                 '"threads [provider]"')

    async def run(self, prompt: str = "", **kwargs) -> str:
        args = prompt.strip().split()
        if not args:
            payload = await _fetch("all")
            if payload is None:
                return f"[usage] {CLI_BIN} returned no JSON. Try /usage raw to debug."
            return _format_all(payload)

        cmd = args[0].lower()

        if cmd in ("check", "all", "list"):
            payload = await _fetch("all")
            return _format_all(payload) if payload else "[usage] no data"

        if cmd == "today":
            payload = await _fetch("all", today=True)
            return _format_all(payload) if payload else "[usage] no data"

        if cmd == "days":
            try:
                n = int(args[1]) if len(args) > 1 else 30
            except ValueError:
                n = 30
            payload = await _fetch("all", days=n)
            return _format_all(payload) if payload else "[usage] no data"

        if cmd == "threads":
            provider = args[1].lower() if len(args) > 1 else None
            if provider in PROVIDERS:
                payload = await _fetch(provider)
                if payload is None:
                    return "[usage] no data"
                # Single-provider response uses a different shape
                rep = payload if payload.get("provider") != "all" else None
                if rep is None:
                    reps = payload.get("reports") or []
                    rep = reps[0] if reps else None
                return _format_threads(rep)
            payload = await _fetch("all")
            return _format_threads(None, all_payload=payload) if payload else "[usage] no data"

        if cmd == "raw":
            rc, out, err = await _run(["--provider", "all", "--json"])
            if not out:
                return f"[usage rc={rc}] {err[:500]}"
            return out[:3500]

        if cmd in PROVIDERS:
            payload = await _fetch(cmd)
            if payload is None:
                return "[usage] no data"
            # Single-provider call returns the report object directly
            rep = payload if payload.get("provider") == cmd else None
            if rep is None:
                reps = payload.get("reports") or []
                rep = reps[0] if reps else None
            if rep is None:
                return "[usage] no report for that provider"
            return _format_single(rep)

        return ("Subcommands: check | <provider> | today | days <n> | "
                "threads [provider] | raw\n"
                f"Providers: {', '.join(sorted(PROVIDERS))}")


register(UsageSkill())
