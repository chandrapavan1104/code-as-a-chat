"""Model and engine capability profiles.

Each engine/model combination has known characteristics: supported task types,
context windows, tool access, reasoning levels, performance patterns. These are
used to filter candidates and score them against work-order requirements.

Profiles are populated from:
- Engine capabilities (hardcoded: claude, codex, gemini, qwen)
- User prefs (model selections, backups)
- Config (reasoning level, context size, concurrency)
- Dynamic state (auth, quota)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

log = logging.getLogger("routing_profiles")


@dataclass
class ReasoningProfile:
    """Reasoning level: model family, token budget, latency, success rate."""
    level: Literal["basic", "standard", "advanced", "max"]
    token_budget: int           # input + output estimate
    latency_estimate_sec: float
    success_rate: float         # empirical: 0.8 = 80% success
    cost_factor: float          # 1.0 = baseline; 2.0 = 2x cost


@dataclass
class ModelProfile:
    """Capability profile for an engine/model combination."""
    engine: str                 # "claude", "codex", "gemini", "qwen"
    model_name: str             # "opus", "gpt-5", "2.5-pro", "qwen2.5:7b"

    # Supported task types
    supports_coding: bool = True
    supports_research: bool = True
    supports_tools: bool = True  # file access, shell, etc.

    # Capacity
    context_window: int = 100_000     # tokens
    max_turn_tokens: int = 4_000      # per-request generation limit
    concurrent_capacity: int = 1      # how many simultaneous jobs safe?

    # Operational
    available: bool = True            # auth, account status
    enabled: bool = True              # user preference

    # Performance (from ledger or defaults)
    latency_estimate_sec: float = 30.0
    success_rate: float = 0.85        # empirical from cli_runs_store
    rework_rate: float = 0.05         # fraction needing a second attempt
    token_efficiency: float = 1.0     # lower = fewer tokens for equivalent output

    # Reasoning levels available
    reasoning_levels: dict[str, ReasoningProfile] = field(default_factory=dict)

    # Cost per 1M tokens (for budget calculations)
    input_cost_per_m: float = 3.0
    output_cost_per_m: float = 15.0

    def cost_estimate(self, estimated_tokens: int = 10_000) -> float:
        """Rough cost for a job of typical size."""
        # Guess: 70% input, 30% output; use token_efficiency to scale
        in_tokens = estimated_tokens * 0.7 / self.token_efficiency
        out_tokens = estimated_tokens * 0.3
        return (in_tokens / 1e6) * self.input_cost_per_m + \
               (out_tokens / 1e6) * self.output_cost_per_m


def _claude_profiles() -> dict[str, ModelProfile]:
    """Claude models by tier."""
    base_reasoning = {
        "basic": ReasoningProfile("basic", 50_000, 15.0, 0.92, 1.0),
        "standard": ReasoningProfile("standard", 100_000, 25.0, 0.95, 1.2),
        "advanced": ReasoningProfile("advanced", 150_000, 45.0, 0.97, 2.0),
    }
    return {
        "claude-opus": ModelProfile(
            engine="claude", model_name="opus",
            context_window=200_000, max_turn_tokens=4_000, concurrent_capacity=1,
            success_rate=0.92, rework_rate=0.04, token_efficiency=0.85,
            reasoning_levels=base_reasoning,
            input_cost_per_m=15.0, output_cost_per_m=75.0,
        ),
        "claude-sonnet": ModelProfile(
            engine="claude", model_name="sonnet",
            context_window=200_000, max_turn_tokens=4_000, concurrent_capacity=2,
            success_rate=0.88, rework_rate=0.05, token_efficiency=1.0,
            reasoning_levels=base_reasoning,
            input_cost_per_m=3.0, output_cost_per_m=15.0,
        ),
        "claude-haiku": ModelProfile(
            engine="claude", model_name="haiku",
            context_window=100_000, max_turn_tokens=2_000, concurrent_capacity=4,
            success_rate=0.80, rework_rate=0.08, token_efficiency=1.2,
            reasoning_levels={
                "basic": ReasoningProfile("basic", 30_000, 5.0, 0.85, 1.0),
                "standard": ReasoningProfile("standard", 50_000, 12.0, 0.88, 1.1),
            },
            input_cost_per_m=0.80, output_cost_per_m=4.0,
        ),
    }


def _codex_profiles() -> dict[str, ModelProfile]:
    """Codex models (read from config + live model cache)."""
    return {
        "codex-gpt-5.6-sol": ModelProfile(
            engine="codex", model_name="gpt-5.6-sol",
            context_window=200_000, max_turn_tokens=8_000, concurrent_capacity=2,
            success_rate=0.90, rework_rate=0.04, token_efficiency=0.9,
            latency_estimate_sec=40.0,
            reasoning_levels={
                "basic": ReasoningProfile("basic", 80_000, 30.0, 0.90, 1.0),
                "standard": ReasoningProfile("standard", 150_000, 50.0, 0.93, 1.3),
            },
            input_cost_per_m=8.0, output_cost_per_m=24.0,
        ),
        "codex-gpt-5.5": ModelProfile(
            engine="codex", model_name="gpt-5.5",
            context_window=180_000, max_turn_tokens=6_000, concurrent_capacity=2,
            success_rate=0.87, rework_rate=0.05, token_efficiency=1.0,
            latency_estimate_sec=35.0,
            reasoning_levels={
                "basic": ReasoningProfile("basic", 60_000, 25.0, 0.87, 1.0),
            },
            input_cost_per_m=6.0, output_cost_per_m=18.0,
        ),
        "codex-gpt-5.4-mini": ModelProfile(
            engine="codex", model_name="gpt-5.4-mini",
            context_window=128_000, max_turn_tokens=4_000, concurrent_capacity=3,
            success_rate=0.82, rework_rate=0.08, token_efficiency=1.2,
            latency_estimate_sec=20.0,
            reasoning_levels={
                "basic": ReasoningProfile("basic", 40_000, 15.0, 0.82, 1.0),
            },
            input_cost_per_m=3.0, output_cost_per_m=9.0,
        ),
    }


def _gemini_profiles() -> dict[str, ModelProfile]:
    """Gemini models."""
    return {
        "gemini-2.5-pro": ModelProfile(
            engine="gemini", model_name="gemini-2.5-pro",
            context_window=1_000_000, max_turn_tokens=8_000, concurrent_capacity=2,
            success_rate=0.89, rework_rate=0.05, token_efficiency=0.85,
            latency_estimate_sec=45.0,
            reasoning_levels={
                "basic": ReasoningProfile("basic", 100_000, 35.0, 0.89, 1.0),
                "advanced": ReasoningProfile("advanced", 200_000, 60.0, 0.92, 1.5),
            },
            input_cost_per_m=1.25, output_cost_per_m=5.0,
        ),
        "gemini-2.5-flash": ModelProfile(
            engine="gemini", model_name="gemini-2.5-flash",
            context_window=1_000_000, max_turn_tokens=6_000, concurrent_capacity=3,
            success_rate=0.84, rework_rate=0.07, token_efficiency=1.0,
            latency_estimate_sec=15.0,
            reasoning_levels={
                "basic": ReasoningProfile("basic", 60_000, 8.0, 0.84, 1.0),
            },
            input_cost_per_m=0.075, output_cost_per_m=0.3,
        ),
    }


def _qwen_profiles() -> dict[str, ModelProfile]:
    """Local Ollama Qwen (offline, free)."""
    return {
        "qwen-2.5-7b": ModelProfile(
            engine="qwen", model_name="qwen2.5:7b",
            context_window=32_000, max_turn_tokens=2_000, concurrent_capacity=1,
            supports_tools=False,  # local only, no file/shell access in night jobs
            success_rate=0.75, rework_rate=0.12, token_efficiency=1.4,
            latency_estimate_sec=60.0,
            reasoning_levels={
                "basic": ReasoningProfile("basic", 30_000, 40.0, 0.75, 1.0),
            },
            input_cost_per_m=0.0, output_cost_per_m=0.0,
        ),
        "qwen-2.5-3b": ModelProfile(
            engine="qwen", model_name="qwen2.5:3b",
            context_window=16_000, max_turn_tokens=1_500, concurrent_capacity=2,
            supports_tools=False,
            success_rate=0.70, rework_rate=0.15, token_efficiency=1.5,
            latency_estimate_sec=30.0,
            reasoning_levels={
                "basic": ReasoningProfile("basic", 15_000, 20.0, 0.70, 1.0),
            },
            input_cost_per_m=0.0, output_cost_per_m=0.0,
        ),
    }


class ProfileRegistry:
    """Central store of model profiles, keyed by engine/model combo."""

    def __init__(self):
        self._profiles = {}
        # Pre-populate from known models
        for profiles in [_claude_profiles(), _codex_profiles(),
                        _gemini_profiles(), _qwen_profiles()]:
            self._profiles.update(profiles)

    def get(self, engine: str, model: str = "") -> ModelProfile | None:
        """Look up a profile by engine + optional model. Falls back to first
        available model for the engine if model is not specified."""
        if not model:
            # Find first available for this engine
            for key, prof in self._profiles.items():
                if prof.engine == engine.lower():
                    return prof
            return None
        key = f"{engine.lower()}-{model.lower()}"
        return self._profiles.get(key)

    def by_engine(self, engine: str) -> list[ModelProfile]:
        """All profiles for a specific engine, in preference order."""
        engine = engine.lower()
        return sorted(
            [p for p in self._profiles.values() if p.engine == engine],
            key=lambda p: (-p.success_rate, p.latency_estimate_sec)
        )

    def all(self) -> list[ModelProfile]:
        """All registered profiles, sorted by success rate."""
        return sorted(self._profiles.values(),
                     key=lambda p: (-p.success_rate, p.latency_estimate_sec))

    def add(self, profile: ModelProfile) -> None:
        """Register or update a profile."""
        key = f"{profile.engine.lower()}-{profile.model_name.lower()}"
        self._profiles[key] = profile


# Singleton registry
_registry = ProfileRegistry()


def get_registry() -> ProfileRegistry:
    """Fetch the global profile registry."""
    return _registry


def update_from_prefs() -> None:
    """Sync model selections + backup models from prefs into profiles."""
    try:
        from server import prefs
        models = prefs.get_coding_models()
        backups = prefs.get_backup_models()

        # Mark selected models as preferred; others still available
        for engine, model_name in models.items():
            if model_name:
                prof = _registry.get(engine, model_name)
                if prof:
                    prof.enabled = True

        for engine, model_name in backups.items():
            if model_name:
                prof = _registry.get(engine, model_name)
                if prof:
                    # Backup models should be available but not preferred
                    prof.enabled = True
    except Exception as e:
        log.warning("could not sync prefs to profiles: %s", e)


def update_from_usage(usage_pct: dict[str, float], quota_stop_pct: int = 85) -> None:
    """Mark engines as over-quota based on live Codaur usage."""
    for engine, pct in usage_pct.items():
        for prof in _registry.by_engine(engine):
            prof.available = pct < quota_stop_pct
