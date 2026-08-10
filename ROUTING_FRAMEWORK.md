# Capability- and Quota-Aware Routing Framework

**Status: SHADOW MODE** — Recording recommendations without changing live assignments.

## Executive Summary

This framework routes Night Shift jobs to the best-matching engine (Claude, Codex, Gemini) by:
1. **Extracting task features** from the structured WorkOrderSpec (type, languages, complexity, risk)
2. **Hard-filtering** engines by capability, quota, and policy
3. **Scoring** remaining candidates by historical success, token efficiency, task affinity
4. **Confidence-based decisions** — deterministic when clear, optional strong model for ambiguous cases
5. **Shadow mode** — recording recommendations in parallel without changing the live dispatcher

No activation yet. Live routing changes require owner evaluation and explicit approval.

---

## Framework Comparison

### Why Not Adopt External Frameworks?

| Framework | Strengths | Constraints | Verdict |
|-----------|-----------|-------------|---------|
| **LangGraph** | HITL (human-in-the-loop) workflows, state persistence | Requires async event loop refactor; adds network dependency on Anthropic | ❌ Too heavyweight for our CLI-based engines |
| **AutoGen SelectorGroupChat** | Multi-agent discussion & consensus | Requires running multiple agents; quadratic token cost; no fallback to single engine | ❌ Incompatible with subscription-backed CLI quota |
| **LiteLLM** | Unified API, automatic failover, budget tracking | Assumes cloud API access; our engines are local CLIs with session reuse | ⚠️ Good for cost budgeting but not engine selection |
| **LMSYS RouteLLM** | Calls strong LLM to route between weak/strong models | Requires an external "router" model (adds tokens + latency); overkill for 3 engines | ⚠️ Possible future extension; too expensive for MVP |

**Chosen: Lightweight adapter** around existing Night Shift infrastructure.
- Reuses Codaur usage snapshots (already available)
- Respects one-project/one-agent/native-session rules
- No new dependencies; runs synchronously in Night Shift's tick loop
- Optional dispatcher model for ambiguous cases (configurable, token-capped)

---

## Architecture

### Modules

1. **`routing_profiles.py`** — ModelProfile registry
   - Per-engine/model: capabilities, context window, success rate, cost
   - Populated from config + live prefs + usage snapshots
   - Extensible: `ProfileRegistry.add()` for custom models

2. **`routing_features.py`** — Work order feature extraction
   - Parses WorkOrderSpec into task characteristics
   - Detects: languages, frameworks, risk (security, architecture, performance), size, ambiguity
   - Produces: `WorkOrderFeatures` dataclass + human-readable summary

3. **`routing_dispatcher.py`** — Multi-stage router
   - Hard-filter: enabled, auth, capability, quota, context fit, reasoning availability
   - Score: historical success, efficiency, task affinity
   - Confidence: gap between top two candidates
   - Rationale: explainable decision per job

4. **`db/routing_recommendations_store.py`** — Shadow mode ledger
   - SQLite: job_id → (recommended_engine, alternatives, scores, quota snapshot, confidence, rationale)
   - No effect on live assignment; purely observational

5. **`routing_eval.py`** — Evaluation tools
   - Compare recommendations to actual outcomes
   - Accuracy, confidence calibration, cost efficiency metrics
   - Identify feature-outcome correlations

6. **Integration in `night_shift.py`**
   - `_record_shadow_recommendation()` calls dispatcher for every job
   - Stores result without changing `_pick_engine()` return value
   - Zero overhead if dispatcher errors (failures logged, non-blocking)

---

## Routing Matrix: Task Type → Engine Recommendation

### By Task Characteristics

| Task Type | Size | Risk | Language/Framework | Recommended Engine | Rationale |
|-----------|------|------|--------------------|--------------------|-----------|
| Bug fix | Patch | Low | Python | **Codex** or **Claude** (small, fast) | High efficiency; Codex cheaper for patches |
| Feature | Feature | Medium | TypeScript + React | **Claude** (Sonnet) | Context for UI, dependency analysis |
| Refactor (schema) | Large | High | SQL + Python | **Claude** (Opus) | Needs architecture thinking, large context |
| UI (Flutter) | Feature | Low | Dart + Flutter | **Claude** (Sonnet/Opus) | Flutter expertise; UI testing needs |
| API design | Large | High | Python + OpenAPI | **Claude** (Opus) | Architecture + documentation |
| Security patch | Patch | Critical | Any | **Claude** (Opus) | Best judgment for security decisions |
| Research | Any | Low | Any | **Claude** (Sonnet) | Reading+synthesis over coding |
| Dependency update | Patch | Low | Any | **Codex** | Fast, deterministic |
| Graphics/rendering | Feature | Medium | C++/Rust | **Claude** (Opus) | Complex spatial reasoning |

### By Engine Strengths

**Claude:**
- ✅ Architecture decisions, security-sensitive tasks, UI design, refactoring
- ✅ Best success rate (92% Opus, 88% Sonnet)
- ✅ Large context window (200K tokens)
- ⚠️ Higher cost; prefer for complex tasks

**Codex:**
- ✅ Patches, small features, deterministic code generation
- ✅ Competitive cost; 5.6-sol model (90% success)
- ✅ Strong on multi-file edits
- ⚠️ Smaller context; skip for architecture

**Gemini:**
- ✅ Massive context (1M tokens); good for large codebases
- ✅ Strong at code analysis; good for research
- ✅ 2.5-Pro comparable to Claude Opus
- ⚠️ Less tuned for deep reasoning

---

## Feature Extraction Examples

### Example 1: Mechanical Patch
```
Title: Fix typo in error message
Outcome: Correct misspelled word
Plan: [Find typo, Replace, Test]
```
**Extracted Features:**
- `is_bugfix: True`
- `estimated_change_size: "patch"` (< 100 LOC)
- `ambiguity_score: 0.0` (very clear)
- `reasoning_level: "basic"`

**Recommendation:**
- Engine: **Codex** (high efficiency for patches)
- Confidence: **0.9** (very clear decision)

### Example 2: Large Architecture Task
```
Title: Migrate to multi-tenant schema
Plan: [Design schema, Migration script, Update API, Update ORM, Tests, Rollout plan]
Policy: Security review required
```
**Extracted Features:**
- `is_architecture_heavy: True`
- `is_security_sensitive: True`
- `estimated_change_size: "large"` (1000+ LOC)
- `requires_db_schema: True`
- `ambiguity_score: 0.2` (structured but complex)
- `reasoning_level: "advanced"`

**Recommendation:**
- Engine: **Claude** (Opus preferred)
- Confidence: **0.85** (clear winner; advanced reasoning needed)

### Example 3: Ambiguous Task
```
Title: Improve performance
Outcome: (empty)
Plan: [Profile code, Optimize]
Acceptance: (empty)
```
**Extracted Features:**
- `ambiguity_score: 0.7` (unclear outcome & acceptance)
- `needs_clarification: True`
- `reasoning_level: "advanced"` (ambiguity bumps it up)

**Recommendation:**
- Engine: **Claude** (best judgment for unclear tasks)
- Confidence: **0.55** (low confidence; might invoke optional dispatcher model)
- Rationale: "Task is ambiguous; recommend human clarification before assignment"

---

## Scoring Algorithm

### Hard Filters (Pass/Fail)
```python
if not engine.enabled or not engine.available:
    skip(engine)  # disabled or over quota

if job_type == "research" and not engine.supports_research:
    skip(engine)
if not engine.supports_tools and job_needs_tools:
    skip(engine)

if job_context_tokens > engine.context_window:
    skip(engine)  # won't fit

if job_reasoning_level not in engine.reasoning_levels:
    skip(engine)  # capability gap
```

### Scoring (0–100 scale)
```python
score = engine.success_rate * 100  # baseline: 0–100

# Task-model affinity
if job.is_bugfix:
    score *= 1.05  # favor high-accuracy models
if job.is_refactor:
    score += engine.context_window / 10_000  # favor spacious models
if job.is_architecture_heavy and engine in (Opus, GPT5.6, Gemini2.5Pro):
    score *= 1.1  # premium models for complex work

# Reasoning match
if job.reasoning_level in engine.reasoning_levels:
    reasoning_success = engine.reasoning_levels[level].success_rate
    score *= (1 + reasoning_success) / 2  # boost if reasoning available
else:
    score *= 0.8  # penalty if level not available

# Token efficiency (for small jobs, prefer cheap models)
if job_estimated_lines < 100:
    score *= (2.0 - engine.token_efficiency)  # favor efficient models
elif job_estimated_lines > 1000:
    score *= engine.token_efficiency  # favor better models

# Context headroom
if engine.context_window / job_context_tokens >= 5:
    score *= 1.05  # bonus for comfortable headroom
elif engine.context_window / job_context_tokens < 2:
    score *= 0.9  # penalty if tight
```

### Confidence Calculation
```python
winner_score = sorted_scores[0][1]
runner_up_score = sorted_scores[1][1] if len(sorted_scores) > 1 else 0

gap = winner_score - runner_up_score
confidence = 0.3 + (gap / 20) * 0.7  # 0–1.0

# High confidence (> 0.8):
#   - 20+ point lead, or only one candidate
# Medium confidence (0.5–0.8):
#   - 10–20 point lead
# Low confidence (< 0.5):
#   - < 10 point lead, ambiguous task
```

---

## Shadow Mode Integration

### Recording Recommendations

**In `night_shift.py`:**
```python
def _pick_engine(job, usage_pct):
    configured = _engines()
    
    # ← NEW: Record shadow recommendation
    if usage_pct:
        _record_shadow_recommendation(job, usage_pct)
    
    # ← UNCHANGED: Live assignment logic
    return _existing_pick_logic(job)
```

**Result:**
- Every job gets a recommendation recorded
- Actual assignment is unchanged
- Zero performance impact if dispatcher errors

### API Exposure

**GET `/api/queue/{job_id}`** now includes:
```json
{
  "id": 123,
  "...": "existing fields",
  "routing_recommendation": {
    "recommended_engine": "claude",
    "confidence": 0.85,
    "alternatives": [["codex", 72.5], ["gemini", 65.0]],
    "features_summary": "Large architecture | SQL, Python | 🏗️ Architecture-heavy | 🧠 Reasoning: advanced",
    "rationale": "Recommended: CLAUDE\nConfidence: HIGH (85%)\n..."
  }
}
```

**App behavior:**
- Displays recommendation in job detail
- (Future) Toggle to use recommendation instead of actual assignment
- (Future) Explanation card with features + alternatives

---

## Evaluation Methodology

### Metrics

1. **Accuracy** — Did recommendation match actual assignment?
   - Target: > 80% by job type (mechanical, architecture, UI, research, etc.)

2. **Confidence Calibration** — Was confidence correlated with correctness?
   - High confidence (> 0.8) should be right > 90% of the time
   - Low confidence (< 0.5) should be wrong ~50% of the time

3. **Cost Efficiency** — Would recommendation have saved tokens?
   - Compare recommended model's token efficiency vs. actual
   - Example: Recommend Codex (10% efficiency) instead of Claude (5% efficiency) for patches

4. **Success Rate Match** — Did jobs recommended to engine X actually succeed?
   - Should align with engine's empirical success_rate
   - If divergence > 5%, adjust profile

### Evaluation Command

```bash
# List recent recommendations (50)
python3 -c "
from server.db import routing_recommendations_store
from server.db import night_queue_store
from server import routing_eval

recs = routing_recommendations_store.list_recent(50)
jobs_by_id = {j['id']: j for j in night_queue_store.list_jobs(limit=200)}
results = routing_eval.batch_evaluate(recs, jobs_by_id)
summary = routing_eval.summarize_eval(results)

print('Accuracy:', f\"{summary['accuracy']:.1%}\")
print('Avg Confidence:', f\"{summary['avg_confidence']:.1%}\")
print('Success Rate:', f\"{summary['success_rate']:.1%}\")
print('By Engine:')
for eng, stats in summary.get('by_engine', {}).items():
    print(f\"  {eng}: {stats['accuracy']:.1%} accuracy, {stats['success_rate']:.1%} success\")
"
```

### Criteria for Live Activation

Before enabling recommendations as live assignments, verify:

✅ **Accuracy > 80%** across all engine types
✅ **Confidence calibration** (high confidence correct > 90%, low confidence wrong ~50%)
✅ **No regression** in job success rate vs. current simple dispatcher
✅ **Cost savings** >= 5% token efficiency (optional; cost-neutral acceptable)
✅ **Edge cases handled** (quota exhaustion, unavailable engines, user pins)
✅ **No breaking changes** to one-project/one-agent/native-session rules

If any metric fails:
1. Analyze divergence (e.g., "Claude Opus over-recommended for patches")
2. Adjust scoring weights or profile configurations
3. Re-evaluate on fixture data
4. Document findings in this file
5. Re-submit for review

---

## Test Fixtures (Reproducible Routing)

See `server/tests/test_routing.py`:
- `test_routing_mechanical_patch()` — Small, clear bugfix
- `test_routing_large_architecture()` — Multi-step refactor
- `test_routing_ui_task()` — Flutter UI change
- `test_routing_research_task()` — Read-only research
- `test_routing_unavailable_engine()` — One engine over quota
- `test_routing_all_quota_exhausted()` — All engines benched
- `test_routing_user_pinned_engine()` — User-pinned engine
- `test_routing_reasoning_level()` — Advanced reasoning selection
- `test_feature_extraction_accuracy()` — Feature parsing
- `test_confidence_calibration()` — Clear vs. ambiguous tasks

**Run tests:**
```bash
pytest server/tests/test_routing.py -v
# (or without pytest: python3 server/tests/test_routing.py)
```

---

## Configuration & Tuning

### Profile Customization

Edit scoring weights in `routing_dispatcher.py` `_score_candidates()`:

```python
# Example: boost Claude Sonnet for small tasks
if job.estimated_lines < 100 and profile.model_name == "sonnet":
    score *= 1.15
```

### Reasoning Level Availability

Edit `routing_profiles.py` per engine:

```python
"claude-opus": ModelProfile(
    reasoning_levels={
        "basic": ReasoningProfile(...),
        "standard": ReasoningProfile(...),
        "advanced": ReasoningProfile(...),  # Add if Claude 5 supports reasoning
        "max": ReasoningProfile(...),
    },
    ...
)
```

### Quota Thresholds

Edit in prefs or `.env`:
```bash
NIGHT_QUOTA_STOP_PCT=85  # bench engine at 85% of rate-limit window
```

---

## Future Work

1. **Dispatcher Model** (optional, configurable)
   - Call a strong LLM (Claude 3.5 Sonnet) to break ties when gap < 10 points
   - Structured rationale: "Choose X because Y"
   - Token budget: 1K max per tie-break (configurable)

2. **Live Activation**
   - Flip `ROUTING_SHADOW_MODE=false` to use recommendations as assignments
   - Gradual rollout: 10% → 25% → 50% → 100%
   - Monitoring: alert if success rate drops

3. **Learned Weights**
   - Analyze eval results, adjust scoring by task type
   - Example: "patches performed 5% worse than expected on Claude; adjust success_rate down"

4. **Multi-Agent Editing**
   - Prevent simultaneous assignment to same repo
   - Coordinate fix agent + queue jobs

5. **Research Task Routing**
   - Special profile for research (no tools, read-only)
   - Prefer breadth (Gemini) over depth (Claude Opus)

---

## Questions & Answers

**Q: Why shadow mode first?**
A: Builds confidence in the dispatcher before it affects job outcomes. Allows measuring accuracy without risk.

**Q: What if all engines are filtered out?**
A: Falls back to first configured engine (current behavior) with low confidence (0.3). Logs a warning.

**Q: Why not use LangGraph?**
A: Adds async/event infrastructure we don't need; our CLI engines are simpler than multi-agent systems.

**Q: How do I turn recommendations into live assignments?**
A: Owner review + approval. Edit `night_shift.py` to call `decision.recommended_engine` instead of `_pick_engine()`.

**Q: Can the dispatcher model be claude-haiku?**
A: Yes, edit `routing_dispatcher.py` to use `SHELL_MODEL` or a dedicated config.

---

## Files Changed

- ✨ `server/routing_profiles.py` — ModelProfile registry
- ✨ `server/routing_features.py` — Feature extraction
- ✨ `server/routing_dispatcher.py` — Multi-stage router
- ✨ `server/db/routing_recommendations_store.py` — Shadow ledger
- ✨ `server/routing_eval.py` — Evaluation tools
- ✨ `server/tests/test_routing.py` — Test fixtures
- 🔧 `server/night_shift.py` — Shadow mode integration
- 🔧 `server/api_v2.py` — Expose recommendations in job detail

---

**Status: SHADOW MODE**
Recommendations recorded, not applied. Ready for offline evaluation and tuning.
