"""Extract routing features from a work order.

Analyze a WorkOrderSpec to classify the task and estimate its requirements:
- programming languages and frameworks
- change size (patch, feature, refactor)
- ambiguity (unclear requirements, design decisions needed)
- risk (security, architecture, database changes)
- special needs (UI testing, browser, graphics)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Literal

log = logging.getLogger("routing_features")


@dataclass
class WorkOrderFeatures:
    """Extracted task characteristics for routing."""
    # Classification
    work_type: Literal["coding", "research"] = "coding"
    is_bugfix: bool = False
    is_refactor: bool = False

    # Languages/frameworks detected (lowercased)
    languages: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    requires_tools: bool = True  # shell, package managers, etc.

    # Size estimate: lines of code or files changed
    estimated_change_size: Literal["patch", "feature", "refactor", "large"] = "patch"
    estimated_lines: int = 50  # rough guess

    # Complexity signals
    is_architecture_heavy: bool = False
    is_security_sensitive: bool = False
    is_performance_critical: bool = False
    requires_db_schema: bool = False

    # UI/presentation needs
    requires_ui_testing: bool = False
    requires_browser: bool = False
    requires_graphics: bool = False

    # Clarity
    ambiguity_score: float = 0.0  # 0.0 = clear, 1.0 = very unclear
    needs_clarification: bool = False

    # Reasoning/analysis required
    requires_research: bool = False
    reasoning_level: Literal["basic", "standard", "advanced", "max"] = "standard"

    # Deadlines and context
    is_urgent: bool = False
    estimated_context_tokens: int = 5_000  # code size + docs


_LANGUAGE_PATTERNS = {
    "python": r"\bpython\b",
    "typescript": r"\b(typescript|tsx?)\b",
    "javascript": r"\b(javascript|jsx?)\b",
    "go": r"\bgo(?:lang)?\b",
    "rust": r"\brust\b",
    "java": r"\bjava\b",
    "kotlin": r"\bkotlin\b",
    "swift": r"\bswift\b",
    "dart": r"\bdart\b",
    "sql": r"\b(sql|postgres|mysql|sqlite)\b",
    "bash": r"\bbash\b",
    "shell": r"\b(shell|sh)\b",
    "c++": r"\bc\+\+\b",
    "c": r"\bc\b",
}

_FRAMEWORK_PATTERNS = {
    "flutter": r"\bflutter\b",
    "django": r"\bdjango\b",
    "fastapi": r"\bfastapi\b",
    "react": r"\breact\b",
    "nextjs": r"\bnext(?:\.)?js\b",
    "vue": r"\bvue\b",
    "angular": r"\bangular\b",
    "spring": r"\bspring\b",
    "rails": r"\brails\b",
    "express": r"\bexpress\b",
    "flask": r"\bflask\b",
}

_RISK_KEYWORDS = {
    "security": ("security", "authentication", "authorization", "credential",
                 "encryption", "hash", "password", "token", "xss", "sql injection",
                 "vulnerability", "cve"),
    "architecture": ("architecture", "design", "refactor", "migration", "schema",
                     "database", "api", "endpoint", "service"),
    "performance": ("performance", "optimization", "cache", "latency", "throughput",
                    "scaling", "load", "stress test", "benchmark"),
}

_SIZE_KEYWORDS = {
    "patch": ("fix", "bug", "typo", "small", "minor", "one-liner"),
    "feature": ("add", "feature", "implement", "new", "endpoint", "page"),
    "refactor": ("refactor", "restructure", "rename", "reorganize", "rewrite"),
    "large": ("rewrite", "rebuild", "complete", "overhaul", "reimplement"),
}


def extract_features(spec_dict: dict) -> WorkOrderFeatures:
    """Parse a WorkOrderSpec dict and extract routing features."""
    text = _combine_text(spec_dict)
    text_lower = text.lower()

    features = WorkOrderFeatures()
    features.work_type = spec_dict.get("work_type", "coding")

    # Languages and frameworks
    features.languages = _detect_languages(text_lower)
    features.frameworks = _detect_frameworks(text_lower)

    # Estimate change size
    features.estimated_change_size = _estimate_size(text_lower, spec_dict)
    features.estimated_lines = _estimate_lines(text_lower, features.estimated_change_size)

    # Risk and complexity signals
    features.is_bugfix = any(k in text_lower for k in ("bug", "fix", "error"))
    features.is_refactor = any(k in text_lower for k in ("refactor", "restructure", "rewrite"))
    features.is_architecture_heavy = _has_risk_keywords(text_lower, "architecture")
    features.is_security_sensitive = _has_risk_keywords(text_lower, "security")
    features.is_performance_critical = _has_risk_keywords(text_lower, "performance")
    features.requires_db_schema = any(k in text_lower for k in ("database", "schema", "migration"))

    # UI and testing needs
    features.requires_ui_testing = any(
        k in text_lower for k in ("ui", "ux", "screenshot", "visual", "layout")
    ) or "flutter" in features.frameworks
    features.requires_browser = any(
        k in text_lower for k in ("browser", "web", "frontend", "react", "vue", "html")
    )
    features.requires_graphics = any(
        k in text_lower for k in ("graphics", "canvas", "image", "render", "svg")
    )

    # Ambiguity: count unclear signals
    unclear_signals = 0
    if "unclear" in text_lower or "decide" in text_lower or "question" in text_lower:
        unclear_signals += 1
    if not spec_dict.get("acceptance"):
        unclear_signals += 1
    if len(spec_dict.get("plan", [])) < 2:
        unclear_signals += 1
    features.ambiguity_score = min(0.8, unclear_signals * 0.3)
    features.needs_clarification = features.ambiguity_score > 0.5

    # Reasoning level: bump up for complex tasks
    if features.is_architecture_heavy or features.ambiguity_score > 0.5:
        features.reasoning_level = "advanced"
    elif features.is_security_sensitive or features.requires_research:
        features.reasoning_level = "standard"
    else:
        features.reasoning_level = "basic"

    # Urgency
    features.is_urgent = any(k in text_lower for k in ("urgent", "asap", "today", "immediately"))

    # Research classification
    features.requires_research = features.work_type == "research"

    # Context size estimate: rough heuristic
    context_lines = len(text.split("\n"))
    features.estimated_context_tokens = max(5_000, min(50_000, context_lines * 100))

    features.requires_tools = not (features.work_type == "research" and not features.requires_tools)

    return features


def _combine_text(spec_dict: dict) -> str:
    """Merge all spec fields into a single searchable text."""
    parts = [
        spec_dict.get("title", ""),
        spec_dict.get("outcome", ""),
        spec_dict.get("context", ""),
        " ".join(spec_dict.get("plan", [])),
        spec_dict.get("policy", ""),
        " ".join(spec_dict.get("acceptance", [])),
        spec_dict.get("out_of_scope", ""),
        " ".join(spec_dict.get("assumptions", [])),
    ]
    return " ".join(s for s in parts if s)


def _detect_languages(text_lower: str) -> list[str]:
    """Find programming languages mentioned."""
    found = []
    for lang, pattern in _LANGUAGE_PATTERNS.items():
        if re.search(pattern, text_lower):
            found.append(lang)
    return list(dict.fromkeys(found))  # dedupe, preserve order


def _detect_frameworks(text_lower: str) -> list[str]:
    """Find frameworks mentioned."""
    found = []
    for fw, pattern in _FRAMEWORK_PATTERNS.items():
        if re.search(pattern, text_lower):
            found.append(fw)
    return list(dict.fromkeys(found))


def _estimate_size(text_lower: str, spec_dict: dict) -> str:
    """Estimate change magnitude: patch, feature, refactor, large."""
    # Count plan steps as a proxy for scope
    plan_steps = len(spec_dict.get("plan", []))

    # Check for size indicators
    for size, keywords in _SIZE_KEYWORDS.items():
        if any(k in text_lower for k in keywords):
            return size

    # Default by step count
    if plan_steps >= 5:
        return "large"
    if plan_steps >= 3:
        return "feature"
    return "patch"


def _estimate_lines(text_lower: str, size: str) -> int:
    """Rough estimate of lines of code that will change."""
    estimate = {
        "patch": 50,
        "feature": 300,
        "refactor": 500,
        "large": 1500,
    }.get(size, 100)

    # Adjust for multi-file signals
    if any(k in text_lower for k in ("multiple", "several", "all", "entire")):
        estimate *= 2
    if "database" in text_lower or "schema" in text_lower:
        estimate += 200

    return estimate


def _has_risk_keywords(text_lower: str, risk_type: str) -> bool:
    """Check if a risk category's keywords appear in text."""
    keywords = _RISK_KEYWORDS.get(risk_type, ())
    return any(k in text_lower for k in keywords)


def summarize_features(features: WorkOrderFeatures) -> str:
    """Human-readable summary of features."""
    parts = []

    # Type and size
    size_icon = {"patch": "🔧", "feature": "🎯", "refactor": "♻️", "large": "🚀"}
    parts.append(f"{size_icon.get(features.estimated_change_size, '•')} {features.estimated_change_size.title()}")

    # Languages
    if features.languages:
        parts.append(f"🗣️  {', '.join(features.languages)}")

    # Risk signals
    if features.is_security_sensitive:
        parts.append("🔒 Security-sensitive")
    if features.is_architecture_heavy:
        parts.append("🏗️  Architecture-heavy")
    if features.is_performance_critical:
        parts.append("⚡ Performance-critical")

    # Clarity
    if features.ambiguity_score > 0.5:
        parts.append(f"❓ Ambiguous (score: {features.ambiguity_score:.1f})")

    # Reasoning
    if features.reasoning_level in ("advanced", "max"):
        parts.append(f"🧠 Reasoning: {features.reasoning_level}")

    return " | ".join(parts) if parts else "Standard coding task"
