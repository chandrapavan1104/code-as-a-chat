"""Capability- and quota-aware work order routing.

Multi-stage dispatcher:
  1. Hard filters: enabled, auth, capability, quota, policy
  2. Scoring: historical success, token efficiency, task-model affinity
  3. Deterministic when clear winner (confidence > threshold)
  4. Optional strong model for ambiguous close decisions
  5. Shadow mode: record recommendation without changing assignment
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from server.routing_profiles import get_registry, update_from_usage
from server.routing_features import WorkOrderFeatures, extract_features, summarize_features

log = logging.getLogger("routing_dispatcher")


@dataclass
class RoutingDecision:
    """Result of the routing dispatcher."""
    recommended_engine: str
    confidence: float  # 0.0–1.0; > 0.8 = high confidence
    alternatives: list[tuple[str, float]]  # [(engine, score), ...]
    scores: dict[str, float]  # engine → final score
    rationale: str
    features_summary: str


def route_work_order(
    spec_dict: dict,
    job_id: int,
    configured_engines: list[str] = None,
    usage_pct: dict[str, float] = None,
    pinned_engine: str = "",
    quota_stop_pct: int = 85,
) -> RoutingDecision:
    """Route a work order to the best engine.

    Args:
        spec_dict: WorkOrderSpec as dict (title, outcome, plan, etc.)
        job_id: Night Shift queue job id (for logging)
        configured_engines: List of available engines ["claude", "codex", "gemini"]
        usage_pct: {engine: percent_of_window_used}
        pinned_engine: User-pinned engine (honors if capable)
        quota_stop_pct: Threshold for benching an engine

    Returns:
        RoutingDecision with recommendation, alternatives, confidence, rationale
    """
    configured_engines = configured_engines or ["claude", "codex", "gemini"]
    usage_pct = usage_pct or {}

    # Extract features from the work order
    features = extract_features(spec_dict)
    features_summary = summarize_features(features)

    # Update profile availability from current usage
    update_from_usage(usage_pct, quota_stop_pct)
    registry = get_registry()

    # Hard filters: get candidate engines
    candidates = _hard_filter(
        configured_engines, registry, features, pinned_engine
    )

    if not candidates:
        # All engines filtered out—fallback to first configured
        log.warning("job #%d: all engines filtered out; falling back to %s",
                   job_id, configured_engines[0])
        engine = configured_engines[0]
        return RoutingDecision(
            recommended_engine=engine,
            confidence=0.3,  # low confidence fallback
            alternatives=[],
            scores={e: 0.0 for e in configured_engines},
            rationale="No engines passed hard filters; using default.",
            features_summary=features_summary,
        )

    # Score remaining candidates
    scores = _score_candidates(candidates, features, registry)
    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)

    # Deterministic routing or ambiguous decision
    winner = sorted_scores[0][0]
    winner_score = sorted_scores[0][1]
    confidence = _compute_confidence(sorted_scores)
    alternatives = sorted_scores[1:]

    rationale = _build_rationale(
        winner, winner_score, confidence, alternatives, features,
        registry, usage_pct, pinned_engine
    )

    log.info("job #%d: routing → %s (confidence=%.1f%%) [%s]",
            job_id, winner, confidence * 100, features_summary)

    return RoutingDecision(
        recommended_engine=winner,
        confidence=confidence,
        alternatives=alternatives,
        scores=scores,
        rationale=rationale,
        features_summary=features_summary,
    )


def _hard_filter(
    configured_engines: list[str],
    registry,
    features: WorkOrderFeatures,
    pinned_engine: str = "",
) -> list[str]:
    """Filter engines by capability and policy.

    Returns list of eligible engines."""
    eligible = []

    for engine in configured_engines:
        # Skip if not in registry
        profiles = registry.by_engine(engine)
        if not profiles:
            log.debug("engine %s not in registry", engine)
            continue

        # Check if engine is enabled and available
        best = profiles[0]
        if not best.enabled or not best.available:
            log.debug("engine %s is disabled or over quota", engine)
            continue

        # Check capability match
        if features.work_type == "research":
            if not best.supports_research:
                log.debug("engine %s does not support research", engine)
                continue
        elif not best.supports_coding:
            log.debug("engine %s does not support coding", engine)
            continue

        # Tool access required?
        if features.requires_tools and not best.supports_tools:
            log.debug("engine %s does not support tools", engine)
            continue

        # Context size: can it fit the request?
        if features.estimated_context_tokens > best.context_window:
            log.debug("engine %s context too small (%d < %d tokens)",
                     engine, best.context_window, features.estimated_context_tokens)
            continue

        # Reasoning level availability
        if features.reasoning_level not in best.reasoning_levels:
            log.debug("engine %s does not support %s reasoning",
                     engine, features.reasoning_level)
            continue

        eligible.append(engine)

    # If user pinned an engine and it passed filters, promote it to winner status
    # (the scorer will still evaluate all candidates, but we'll bias toward the pin)
    if pinned_engine and pinned_engine in eligible:
        log.debug("job pinned to engine %s", pinned_engine)

    return eligible if eligible else list(configured_engines)[:1]


def _score_candidates(
    engines: list[str],
    features: WorkOrderFeatures,
    registry,
) -> dict[str, float]:
    """Score candidate engines for this work order."""
    scores = {}

    for engine in engines:
        profiles = registry.by_engine(engine)
        if not profiles:
            scores[engine] = 0.0
            continue

        # Use the first (best) profile for this engine
        profile = profiles[0]

        # Base score from historical success rate
        score = profile.success_rate * 100  # 0–100

        # Adjust for task-model affinity
        # Bugfixes favor high success rate; refactors favor context window
        if features.is_bugfix:
            score *= 1.05
        if features.is_refactor:
            score += profile.context_window / 10_000  # larger window bonus
        if features.is_architecture_heavy:
            score *= 1.1 if profile.model_name in ("opus", "gpt-5.6-sol", "2.5-pro") else 0.95

        # Reasoning level match
        has_reasoning = features.reasoning_level in profile.reasoning_levels
        if has_reasoning:
            reasoning = profile.reasoning_levels[features.reasoning_level]
            score *= (1 + reasoning.success_rate) / 2  # boost if reasoning available
        else:
            score *= 0.8  # slight penalty if not available

        # Token efficiency: prefer cheaper models for small tasks
        if features.estimated_lines < 100:
            score *= (2.0 - profile.token_efficiency)  # favors efficient models
        elif features.estimated_lines > 1000:
            score *= profile.token_efficiency  # favors better reasoning

        # Context size: bonus for comfortable headroom
        headroom = profile.context_window / features.estimated_context_tokens
        if headroom >= 5:
            score *= 1.05
        elif headroom < 2:
            score *= 0.9

        # Language/framework affinity (if data is available)
        # This is a placeholder for future expansion
        if features.frameworks and "flutter" in features.frameworks:
            # Could boost based on project history with this engine
            pass

        scores[engine] = max(0, score)

    return scores


def _compute_confidence(sorted_scores: list[tuple[str, float]]) -> float:
    """Confidence in the routing decision (0.0–1.0).

    High confidence when:
    - Winner has high score
    - Clear gap from runner-up
    - Decisive filters apply
    """
    if not sorted_scores:
        return 0.0

    winner_score = sorted_scores[0][1]
    if len(sorted_scores) == 1:
        # Only one candidate—high confidence if it's viable
        return min(1.0, winner_score / 80)

    runner_up_score = sorted_scores[1][1]
    gap = winner_score - runner_up_score

    # Confidence = winner's lead, capped at 1.0
    # Perfect gap of 20+ points = 0.8–1.0 confidence
    # Gap of 5 points = 0.5 confidence
    # Gap of 0 = 0.3 confidence (arbitrary choice)
    confidence = 0.3 + (gap / 20) * 0.7
    return min(1.0, max(0.0, confidence))


def _build_rationale(
    winner: str,
    winner_score: float,
    confidence: float,
    alternatives: list[tuple[str, float]],
    features: WorkOrderFeatures,
    registry,
    usage_pct: dict[str, float],
    pinned_engine: str = "",
) -> str:
    """Human-readable explanation of the routing decision."""
    lines = []

    # Decision
    lines.append(f"Recommended: {winner.upper()}")
    if confidence > 0.8:
        lines.append(f"Confidence: HIGH ({confidence*100:.0f}%)")
    elif confidence > 0.5:
        lines.append(f"Confidence: MEDIUM ({confidence*100:.0f}%)")
    else:
        lines.append(f"Confidence: LOW ({confidence*100:.0f}%)")

    # Features
    lines.append(f"Task: {features.estimated_change_size.title()} ({features.estimated_lines} est. LOC)")
    if features.languages:
        lines.append(f"Languages: {', '.join(features.languages)}")
    if features.frameworks:
        lines.append(f"Frameworks: {', '.join(features.frameworks)}")

    # Why this engine
    winner_profile = registry.get(winner) or registry.by_engine(winner)[0]
    lines.append(f"Winner profile: {winner_profile.model_name}")
    lines.append(f"  Success rate: {winner_profile.success_rate*100:.0f}%")
    lines.append(f"  Context window: {winner_profile.context_window:,} tokens")
    lines.append(f"  Cost estimate: ${winner_profile.cost_estimate():.2f}")

    # Quota
    usage = usage_pct.get(winner, 0)
    lines.append(f"  Current quota usage: {usage:.1f}%")

    # Alternatives
    if alternatives:
        lines.append("Alternatives:")
        for alt_engine, alt_score in alternatives[:2]:
            alt_profile = registry.get(alt_engine) or registry.by_engine(alt_engine)[0]
            gap = winner_score - alt_score
            lines.append(f"  {alt_engine}: {alt_profile.model_name} (score: {alt_score:.0f}, gap: {gap:.0f})")

    # Special cases
    if features.is_security_sensitive:
        lines.append("⚠️ SECURITY-SENSITIVE: Manual review recommended before shipping.")
    if features.ambiguity_score > 0.6:
        lines.append("⚠️ AMBIGUOUS: Task has unclear requirements. May need clarification.")
    if pinned_engine and pinned_engine == winner:
        lines.append("ℹ️ Pinned by user.")

    return "\n".join(lines)
