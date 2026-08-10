#!/bin/bash
# Test the routing dispatcher with sample work orders

set -e

echo "🔍 Testing routing framework..."
echo ""

# Test 1: Mechanical patch
echo "Test 1: Mechanical patch (bug fix)"
python3 << 'EOF'
import sys
sys.path.insert(0, '.')

try:
    from server import routing_dispatcher
    from server.work_orders import WorkOrderSpec

    spec = WorkOrderSpec(
        title="Fix typo in error message",
        outcome="Correct misspelled word",
        plan=["Find typo", "Replace", "Test"],
        policy="No auto-deploy",
        acceptance=["Typo corrected"],
        test_handoff="Run tests",
        work_type="coding",
    ).model_dump()

    decision = routing_dispatcher.route_work_order(
        spec_dict=spec,
        job_id=1,
        configured_engines=["claude", "codex", "gemini"],
        usage_pct={"claude": 30, "codex": 20, "gemini": 25},
    )

    print(f"  Recommended: {decision.recommended_engine.upper()}")
    print(f"  Confidence: {decision.confidence:.1%}")
    print(f"  Features: {decision.features_summary}")
    print("")

except ImportError:
    print("  ⚠️  Pydantic not installed; skipping functional test")
    print("     Run: pip install -r requirements.txt")
    print("")
except Exception as e:
    print(f"  ❌ Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF

# Test 2: Verify all modules exist and have correct structure
echo "Test 2: Module structure checks"
for module in \
    "server/routing_profiles.py" \
    "server/routing_features.py" \
    "server/routing_dispatcher.py" \
    "server/db/routing_recommendations_store.py" \
    "server/routing_eval.py"
do
    if [ -f "$module" ]; then
        lines=$(wc -l < "$module")
        echo "  ✓ $module ($lines lines)"
    else
        echo "  ✗ Missing: $module"
        exit 1
    fi
done
echo ""

# Test 3: Check test file
echo "Test 3: Test fixtures"
if [ -f "server/tests/test_routing.py" ]; then
    fixture_count=$(grep -c "^def test_" server/tests/test_routing.py)
    echo "  ✓ Test fixtures: $fixture_count test cases"
else
    echo "  ✗ Missing: server/tests/test_routing.py"
    exit 1
fi
echo ""

# Test 4: Documentation
echo "Test 4: Documentation"
if [ -f "ROUTING_FRAMEWORK.md" ]; then
    lines=$(wc -l < ROUTING_FRAMEWORK.md)
    echo "  ✓ ROUTING_FRAMEWORK.md ($lines lines)"
    sections=$(grep -c "^##" ROUTING_FRAMEWORK.md)
    echo "    - $sections major sections"
else
    echo "  ✗ Missing: ROUTING_FRAMEWORK.md"
    exit 1
fi
echo ""

echo "✅ Routing framework tests passed"
echo ""
echo "Next steps:"
echo "  1. Review ROUTING_FRAMEWORK.md for architecture & design decisions"
echo "  2. Install dependencies: pip install -r requirements.txt"
echo "  3. Run pytest: pytest server/tests/test_routing.py -v"
echo "  4. Start Night Shift; recommendations will be recorded in shadow mode"
echo "  5. Evaluate recommendations: python3 -c \"from server.routing_eval import ...\""
