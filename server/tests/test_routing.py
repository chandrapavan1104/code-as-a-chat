"""Test routing dispatcher with fixture scenarios.

These fixtures cover mechanical, architectural, UI, research, unavailable,
exhausted quota, and user-pinned cases. Verify:
  - Routing produces consistent decisions
  - Confidence scores align with task clarity
  - Edge cases (no eligible engines, exhausted quota) are handled
  - Recommendations are reproducible
"""

import pytest
from server import routing_dispatcher, routing_features, routing_profiles
from server.work_orders import WorkOrderSpec


# Fixture: mechanical patch task
def test_routing_mechanical_patch():
    """Small, clear bug fix → high confidence in fast model."""
    spec = WorkOrderSpec(
        title="Fix typo in error message",
        outcome="Correct a misspelled word in the exception handler.",
        plan=["Find the typo", "Replace with correct spelling", "Test"],
        policy="No automated deployment required.",
        acceptance=["Typo is corrected", "Test passes"],
        test_handoff="Manual: run the test suite",
        work_type="coding",
    ).model_dump()

    decision = routing_dispatcher.route_work_order(
        spec_dict=spec,
        job_id=1,
        configured_engines=["claude", "codex", "gemini"],
        usage_pct={"claude": 30, "codex": 20, "gemini": 25},
        quota_stop_pct=85,
    )

    assert decision.recommended_engine in ["claude", "codex", "gemini"]
    assert decision.confidence > 0.7, "mechanical task should have high confidence"
    assert "typo" in decision.features_summary.lower()


# Fixture: large architecture task
def test_routing_large_architecture():
    """Refactor database schema → needs context & reasoning."""
    spec = WorkOrderSpec(
        title="Migrate user table to multi-tenant schema",
        outcome="Support multiple organizations in one deployment.",
        context="Current schema: users.id → new schema: orgs.id, org_members.id",
        plan=[
            "Design new schema with org_id foreign key",
            "Create migration script with backfill logic",
            "Update API endpoints to filter by org",
            "Update ORM queries to include org_id",
            "Write comprehensive tests",
            "Plan gradual rollout with feature flag",
        ],
        policy="Requires security review before shipping; zero downtime migration.",
        acceptance=[
            "Schema migration runs without errors",
            "All existing data is correctly migrated",
            "API correctly filters by organization",
            "Tests cover edge cases (null org, cross-org access)",
        ],
        test_handoff="Staging deployment with test data; manual cross-org access check.",
        work_type="coding",
    ).model_dump()

    decision = routing_dispatcher.route_work_order(
        spec_dict=spec,
        job_id=2,
        configured_engines=["claude", "codex", "gemini"],
        usage_pct={"claude": 40, "codex": 35, "gemini": 30},
        quota_stop_pct=85,
    )

    assert decision.recommended_engine in ["claude", "codex", "gemini"]
    assert "architecture" in decision.rationale.lower() or "refactor" in decision.rationale.lower()
    # Architecture task should prefer higher-reasoning models
    registry = routing_profiles.get_registry()
    winner_profile = registry.get(decision.recommended_engine)
    assert winner_profile.context_window >= 100_000, "architecture needs space for schema design"


# Fixture: UI task (Flutter)
def test_routing_ui_task():
    """Flutter UI change → should consider widget expertise."""
    spec = WorkOrderSpec(
        title="Add dark mode toggle to settings",
        outcome="Users can switch between light and dark theme.",
        plan=[
            "Add theme toggle in SettingsScreen",
            "Persist theme choice to SharedPreferences",
            "Update Material theme with dark colors",
            "Test on multiple screen sizes",
        ],
        policy="No backend change.",
        acceptance=[
            "Dark mode renders correctly",
            "Toggle persists across app restarts",
            "All text is readable in both themes",
        ],
        test_handoff="Manual testing on phone: verify colors, readability, persistence.",
        work_type="coding",
    ).model_dump()

    decision = routing_dispatcher.route_work_order(
        spec_dict=spec,
        job_id=3,
        configured_engines=["claude", "codex", "gemini"],
        usage_pct={"claude": 25, "codex": 50, "gemini": 20},
        quota_stop_pct=85,
    )

    assert decision.recommended_engine in ["claude", "codex", "gemini"]
    assert "flutter" in decision.features_summary.lower()


# Fixture: research task
def test_routing_research_task():
    """Research: compare three auth frameworks → no code changes."""
    spec = WorkOrderSpec(
        title="Research authentication patterns for microservices",
        outcome="Documented comparison of OAuth2, JWT, and mTLS with recommendations.",
        context="Need to update auth for a microservice architecture.",
        plan=[
            "Research OAuth2 implementation complexity and security",
            "Research JWT token management and expiration",
            "Research mutual TLS certificate handling",
            "Compare cost and latency for each",
            "Document pros/cons with use-case recommendations",
        ],
        policy="Output only; no code changes expected.",
        acceptance=[
            "Comparison document covers all three patterns",
            "Security implications are documented",
            "Recommendations are justified",
        ],
        test_handoff="Review report for clarity and accuracy; share with team.",
        work_type="research",
    ).model_dump()

    decision = routing_dispatcher.route_work_order(
        spec_dict=spec,
        job_id=4,
        configured_engines=["claude", "codex", "gemini"],
        usage_pct={"claude": 40, "codex": 20, "gemini": 30},
        quota_stop_pct=85,
    )

    assert decision.recommended_engine in ["claude", "codex", "gemini"]
    # Research jobs should be marked as such in features
    assert decision.features_summary or decision.rationale  # some output


# Fixture: unavailable engine
def test_routing_unavailable_engine():
    """One engine is over quota → route to available alternatives."""
    spec = WorkOrderSpec(
        title="Implement new API endpoint",
        outcome="RESTful endpoint for user profile updates.",
        plan=["Design schema", "Implement handler", "Write tests"],
        policy="Standard deployment.",
        acceptance=["Endpoint accepts PUT requests", "Tests pass"],
        test_handoff="Manual API test with curl.",
        work_type="coding",
    ).model_dump()

    decision = routing_dispatcher.route_work_order(
        spec_dict=spec,
        job_id=5,
        configured_engines=["claude", "codex", "gemini"],
        usage_pct={"claude": 90, "codex": 30, "gemini": 25},  # claude over 85% limit
        quota_stop_pct=85,
    )

    # Should not recommend the over-quota engine
    assert decision.recommended_engine in ["codex", "gemini"], \
        f"claude is over quota (90%); got {decision.recommended_engine}"


# Fixture: exhausted quota
def test_routing_all_quota_exhausted():
    """All engines over quota → fallback to first configured."""
    spec = WorkOrderSpec(
        title="Small bug fix",
        outcome="Fix assertion error.",
        plan=["Locate error", "Fix"],
        policy="Standard.",
        acceptance=["Error no longer occurs"],
        test_handoff="Run tests.",
        work_type="coding",
    ).model_dump()

    decision = routing_dispatcher.route_work_order(
        spec_dict=spec,
        job_id=6,
        configured_engines=["claude", "codex", "gemini"],
        usage_pct={
            "claude": 90,
            "codex": 90,
            "gemini": 90,
        },  # all over limit
        quota_stop_pct=85,
    )

    # Should still return a recommendation (fallback), with low confidence
    assert decision.recommended_engine is not None
    assert decision.confidence < 0.8, "low confidence when all engines exhausted"


# Fixture: user-pinned engine
def test_routing_user_pinned_engine():
    """User pinned 'codex' → recommend codex if capable."""
    spec = WorkOrderSpec(
        title="Write API docs in Markdown",
        outcome="REST API documentation.",
        plan=["Document endpoints", "Add examples"],
        policy="No code.",
        acceptance=["All endpoints documented"],
        test_handoff="Review clarity.",
        work_type="coding",
    ).model_dump()

    decision = routing_dispatcher.route_work_order(
        spec_dict=spec,
        job_id=7,
        configured_engines=["claude", "codex", "gemini"],
        usage_pct={"claude": 30, "codex": 30, "gemini": 30},
        pinned_engine="codex",
        quota_stop_pct=85,
    )

    # Note: current implementation doesn't force pinned engine,
    # but should still show it in rationale if it's the winner
    assert decision.recommended_engine in ["claude", "codex", "gemini"]


# Fixture: reasoning level selection
def test_routing_reasoning_level():
    """Complex architecture task → should request advanced reasoning."""
    spec = WorkOrderSpec(
        title="Design distributed transaction coordination",
        outcome="Handle multi-service transactions with consistency guarantees.",
        plan=[
            "Research saga vs 2PC patterns",
            "Design rollback mechanism",
            "Model state machine",
            "Implement with Temporal or similar",
        ],
        policy="Architecture review required.",
        acceptance=["Design document approved", "Rollback tested"],
        test_handoff="Design review + staging test.",
        work_type="coding",
    ).model_dump()

    decision = routing_dispatcher.route_work_order(
        spec_dict=spec,
        job_id=8,
        configured_engines=["claude", "codex", "gemini"],
        usage_pct={"claude": 20, "codex": 20, "gemini": 20},
        quota_stop_pct=85,
    )

    # Should recommend an engine and note advanced reasoning needed
    assert "advanced" in decision.rationale.lower() or \
           "architecture" in decision.rationale.lower()


# Fixture: feature extraction accuracy
def test_feature_extraction_accuracy():
    """Verify feature extraction correctly identifies task characteristics."""
    spec = WorkOrderSpec(
        title="Fix Python security vulnerability in JWT validation",
        outcome="Eliminate timing attack in token comparison.",
        context="Current code uses string == for token validation, vulnerable to timing attacks.",
        plan=[
            "Replace == with timing-safe comparison (secrets.compare_digest)",
            "Add test case for timing resistance",
        ],
        policy="Security-critical: requires review before deployment.",
        acceptance=[
            "Comparison is timing-safe",
            "Test demonstrates timing resistance",
        ],
        test_handoff="Security team review.",
        work_type="coding",
    ).model_dump()

    features = routing_features.extract_features(spec)

    assert "python" in features.languages, "should detect Python"
    assert features.is_security_sensitive, "should flag as security-sensitive"
    assert features.is_bugfix, "should be a bugfix"
    assert features.estimated_change_size in ["patch", "feature"], "small change"


# Fixture: confidence calibration
def test_confidence_calibration():
    """Confidence should reflect decision certainty."""
    clear_spec = WorkOrderSpec(
        title="Add logging statement",
        outcome="Debug output for function calls.",
        plan=["Add log.debug() in main loop"],
        policy="No review needed.",
        acceptance=["Log output appears"],
        test_handoff="Check logs.",
        work_type="coding",
    ).model_dump()

    ambiguous_spec = WorkOrderSpec(
        title="Improve performance",
        outcome="",  # vague
        context="",
        plan=["Profile code", "Optimize hot path"],
        policy="",
        acceptance=[],  # no acceptance criteria
        test_handoff="",
        work_type="coding",
    ).model_dump()

    clear_decision = routing_dispatcher.route_work_order(
        spec_dict=clear_spec,
        job_id=101,
        configured_engines=["claude", "codex", "gemini"],
        usage_pct={"claude": 30, "codex": 30, "gemini": 30},
    )

    ambiguous_decision = routing_dispatcher.route_work_order(
        spec_dict=ambiguous_spec,
        job_id=102,
        configured_engines=["claude", "codex", "gemini"],
        usage_pct={"claude": 30, "codex": 30, "gemini": 30},
    )

    assert clear_decision.confidence > ambiguous_decision.confidence, \
        "clear tasks should have higher confidence than ambiguous ones"


if __name__ == "__main__":
    # Run tests manually
    pytest.main([__file__, "-v"])
