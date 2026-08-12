"""Evaluation tools for shadow-mode routing recommendations.

Compare recommendations against actual assignments:
  - Accuracy: did we recommend the right engine?
  - Confidence calibration: was confidence aligned with actual success?
  - Cost efficiency: would the recommendation have saved tokens?
  - Identify patterns: what features correlate with recommendation success?
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger("routing_eval")


@dataclass
class EvalResult:
    """Evaluation of a single job's recommendation."""
    job_id: int
    recommended_engine: str
    actual_engine: str
    match: bool  # recommendation == actual
    confidence: float
    confidence_correct: bool  # confidence > 0.8 and match
    success: bool  # job completed successfully
    tokens_billable: int
    features_summary: str


def evaluate_recommendation(
    rec: dict,
    job: dict,
) -> EvalResult:
    """Compare a recommendation against job outcome.

    Args:
        rec: routing_recommendations_store record
        job: night_queue_store record

    Returns:
        EvalResult with match, confidence, success scores
    """
    rec = rec or {}
    job = job or {}

    recommended = rec.get("recommended_engine", "")
    actual = job.get("engine_used", "auto")
    match = recommended == actual
    confidence = rec.get("confidence", 0.0)
    success = job.get("status") in ("shipped", "deployed", "completed")
    confidence_correct = confidence > 0.8 and match

    return EvalResult(
        job_id=job.get("id", 0),
        recommended_engine=recommended,
        actual_engine=actual,
        match=match,
        confidence=confidence,
        confidence_correct=confidence_correct,
        success=success,
        tokens_billable=job.get("tokens_billable", 0),
        features_summary=rec.get("features_summary", ""),
    )


def batch_evaluate(recommendations: list[dict], jobs_by_id: dict[int, dict]) -> list[EvalResult]:
    """Evaluate a batch of recommendations."""
    results = []
    for rec in recommendations:
        job_id = rec.get("job_id")
        job = jobs_by_id.get(job_id)
        if job:
            results.append(evaluate_recommendation(rec, job))
    return results


def summarize_eval(results: list[EvalResult]) -> dict:
    """Aggregate statistics from evaluation results."""
    if not results:
        return {}

    accuracy = sum(1 for r in results if r.match) / len(results)
    avg_confidence = sum(r.confidence for r in results) / len(results)
    confidence_accuracy = sum(1 for r in results if r.confidence_correct) / len(results)
    success_rate = sum(1 for r in results if r.success) / len(results)

    # Breakdown by engine
    by_engine = {}
    for rec in results:
        key = rec.recommended_engine
        if key not in by_engine:
            by_engine[key] = {"count": 0, "matched": 0, "success": 0}
        by_engine[key]["count"] += 1
        if rec.match:
            by_engine[key]["matched"] += 1
        if rec.success:
            by_engine[key]["success"] += 1

    engine_stats = {
        eng: {
            "count": stats["count"],
            "accuracy": stats["matched"] / stats["count"],
            "success_rate": stats["success"] / stats["count"],
        }
        for eng, stats in by_engine.items()
    }

    return {
        "total_evaluated": len(results),
        "accuracy": accuracy,
        "avg_confidence": avg_confidence,
        "confidence_accuracy": confidence_accuracy,
        "success_rate": success_rate,
        "by_engine": engine_stats,
    }


def filter_recommendations(
    recommendations: list[dict],
    *,
    engine: str | None = None,
    min_confidence: float = 0.0,
    max_confidence: float = 1.0,
    status: str | None = None,
) -> list[dict]:
    """Filter recommendations by criteria for targeted analysis."""
    filtered = []
    for rec in recommendations:
        if engine and rec.get("recommended_engine") != engine:
            continue
        conf = rec.get("confidence", 0.0)
        if not (min_confidence <= conf <= max_confidence):
            continue
        if status and not _rec_has_status(rec, status):
            continue
        filtered.append(rec)
    return filtered


def _rec_has_status(rec: dict, status: str) -> bool:
    # Placeholder: could check if recommendation was correct for a certain outcome
    return True


def format_recommendation_report(rec: dict, job: dict) -> str:
    """Human-readable report of a single recommendation + outcome."""
    lines = []
    lines.append(f"Job #{rec.get('job_id')}")
    lines.append(f"  Recommended: {rec.get('recommended_engine').upper()}")
    lines.append(f"  Actual: {job.get('engine_used', 'unknown').upper()}")
    lines.append(f"  Confidence: {rec.get('confidence', 0):.1%}")
    lines.append(f"  Match: {'✓' if rec.get('recommended_engine') == job.get('engine_used') else '✗'}")
    if rec.get("rationale"):
        lines.append(f"  Rationale: {rec['rationale'].splitlines()[0]}")
    if job.get("summary"):
        lines.append(f"  Outcome: {job['summary'][:100]}...")
    return "\n".join(lines)


# For integration testing / CLI evaluation
def load_fixture_evaluation_pair() -> tuple[dict, dict]:
    """Example fixture pair for testing eval logic."""
    return (
        {
            "job_id": 1,
            "recommended_engine": "claude",
            "confidence": 0.85,
            "rationale": "Best context window for architecture task",
            "features_summary": "Large architecture task",
        },
        {
            "id": 1,
            "engine_used": "claude",
            "status": "shipped",
            "summary": "Successfully completed architecture refactor",
            "tokens_billable": 15000,
        },
    )


if __name__ == "__main__":
    rec, job = load_fixture_evaluation_pair()
    result = evaluate_recommendation(rec, job)
    print(f"Match: {result.match}, Confidence: {result.confidence:.1%}, Success: {result.success}")
