# Which project does a turn run in?

Every coding action — `claude`, `codex`, `gemini`, `files`, `context`, a queued
Night Shift job — runs *inside a directory*. Getting that directory wrong is the
most expensive kind of mistake this system can make, so it is worth
understanding exactly how it is decided.

**The rule: the thread owns the project.**

---

## 1. Resolution — where a turn starts

When a turn arrives at `/run` or `/run/stream`, `workspace.for_turn()` picks its
project in this order, first match wins:

| # | Source | Example |
|---|--------|---------|
| 1 | The client's explicit `project` field | `{"project": "deaf-communication-terminal"}` |
| 2 | The slug in the session id, after `::` | `app:tseu3f9r::deaf-communication-terminal` |
| 3 | The persisted default in `~/.codeasachat/state.json` | `workspace_dir` |

Gajala's chat threads are per-directory, so (2) covers the normal case even from
an older APK — `_sidFor()` in `chat_screen.dart` builds the session id from the
directory name, and `workspace.slug()` reverses it. Telegram and one-shot CLI
callers have no thread, so they land on (3).

The chosen path is held in a **`ContextVar`** for the duration of the turn
(`workspace.bound()`). That is what lets a phone turn, a Telegram turn, and a
Night Shift job run in three different projects at the same moment. Read it with
`workspace.active()`.

> **`config.WORKSPACE_DIR` is not the answer to "where am I".** It is only the
> default for new threads. Reading it directly inside a skill is a bug — you
> will see the default, not the turn's project.

## 2. Switching — one change, no going back

Inside a turn the agent may call `projects switch <name>`. That does **not**
touch the global default. It calls `workspace.rebind()`, which:

- moves the current turn to the new project, so every later step in the same
  turn runs there;
- appends a `[[switch:<name>]]` marker to the reply, which Gajala strips and
  uses to move the conversation to that project's thread;
- **refuses any further switch in that turn**, returning
  `Project already switched to X earlier in this turn`.

That refusal is the point. It is not a safety net bolted on top of a prompt — it
is why the failure below cannot recur.

A switch made by *you* — the Projects screen, or a typed `/projects switch` —
also calls `workspace.persist_default()`, because that is an explicit choice
rather than a routing guess.

```
Thread: #deaf-communication-terminal
  └─ every turn starts in ~/Projects/deaf-communication-terminal

agent calls projects(switch other-project)
  → rebinds THIS turn only
  → emits [[switch:other-project]]  →  app moves the thread
  → a second switch this turn is refused
```

## 3. What it costs — the step budget

A turn gets **10 productive steps** (`STEP_BUDGET`), with a hard `MAX_ROUNDS`
loop guard behind it. Not every tool call is productive. These are executed or
corrected but **not charged**:

- args that arrived as a JSON object and had to be repaired (`_coerce_args`)
- an unknown or unregistered tool
- switching to the project you are already in
- an exact repeat of a call already made this turn (the earlier result is
  replayed instead of re-running it)

So a router that flails does not spend your budget. When the budget genuinely
runs out, the turn ends with a summary of what landed and what is outstanding,
and `stop_reason = step_limit`.

## 4. Which brain routes a turn

Routing decisions go through `_provider_chain(task)` in `shell.py`:

| Task | Order |
|------|-------|
| everything, by default | `claude` → `openai` → **`qwen` (last resort)** |
| anything listed in `QWEN_TASKS` | `qwen` → `claude` → `openai` |

`QWEN_TASKS` defaults to **empty**, on purpose. Running `qwen2.5:3b` as the
primary router cost more than the tokens it saved — it emitted off-schema
decisions, copied a placeholder path out of its own system prompt into a real
tool call, and produced garbled replies. Qwen keeps its place at the *end* of
the chain: when Claude and OpenAI are both exhausted, a degraded local answer
beats no answer.

What makes the chain work is `_is_usable_decision`. Each provider's output is
validated, and a provider that fails is **skipped in favour of the next**. It
accepts only a real decision — `done`, `call` with a tool, a bare tool, or an
action that is a registered tool name. It deliberately rejects any other
invented verb; accepting "any non-empty action" is what let bad output stop
escalating and start reaching the user.

Two levers:

```bash
SHELL_LLM_PROVIDER=openai   # conserve Claude — route on gpt-4o-mini instead
QWEN_TASKS=notes,diary      # put simpler formatting work back on the local model
```

The trace records which brains were consulted, e.g. `qwen:rejected -> claude`.

## 5. Debugging a bad turn

Every turn writes a trace to `~/.codeasachat/agent_runs.db`. **Start here** — do
not read `conversations.db` by hand.

**On the phone:** tap the strip under any reply
(`▸ 4 steps · 38s · deaf-communication-terminal`). Dimmed steps were free ones.

**Over HTTP:**

```bash
TOKEN=$(cat ~/.codeasachat/api_token)
# recent turns for a thread
curl -s -H "X-API-Token: $TOKEN" \
  'http://localhost:8000/api/runs?session_id=app:xxx::general' | jq
# one turn in full
curl -s -H "X-API-Token: $TOKEN" \
  http://localhost:8000/api/runs/<run_id> | jq
```

Read, in order:

1. `workspace` on each step — **where did it actually run?**
2. `stop_reason` — `done`, `step_limit`, `llm_error`, `no_action`,
   `duplicate_stop`, `passthrough`, `final_output`.
3. `charged` — a run full of `charged: 0` steps means the router was flailing;
   look at what it was being told, not at the skills.

Each assistant turn in `conversations` also carries its `run_id`, so any reply
in history can be traced back.

## 6. The failure this was built from

Real turn, `conversations.db` #803 — *"switch to the deaf terminal project and
check the status"*:

```
1. projects(switch deaf-communication-terminal) → SWITCHED
2. projects(switch general)                     → SWITCHED BACK
3. projects({"switch":"deaf-communication..."}) → No project matches
4. projects({"switch":"deaf-communication..."}) → No project matches
5. claude({"command":"..."})
6. projects(switch deaf-communication-terminal) → SWITCHED
7. claude(Read the README...)
(stopped at the step limit of 7)
```

Four separate defects, each independently fixed:

1. **The thrash engine.** `shell.py` built its routing hints *once*, before the
   loop. After step 1 switched, every later iteration still read
   `CURRENT DIRECTORY: general` and "if it names another project, ask to
   switch" — so it switched back, then forth. Hints are now rebuilt each
   iteration, and `rebind()` refuses the second switch anyway.
2. **JSON-as-args.** `_decision_text` `json.dumps`'d a dict, so the projects
   skill received `{"switch":"deaf-..."}` as a literal string. `_coerce_args`
   unwraps it to `switch deaf-...`.
3. **Repeated failures.** The duplicate guard only fired for calls that had
   *timed out*, so an identical *failing* call was re-issued. Any exact repeat is
   now replayed.
4. **A fabricated path.** The system prompt's attachment example contained a
   literal `/path/img.jpg`, and the local router copied it into a real call.
   Placeholders are now unmistakable, and paths may only be copied from an actual
   attachment marker.

Regression test: `server/tests/test_project_switching.py` replays this exact
decision sequence.

## Where the code lives

| Concern | File |
|---------|------|
| Resolution, binding, git description | `server/workspace.py` |
| Chat-facing switch + `[[switch:]]` marker | `server/skills/projects.py` |
| Budget, coercion, dedup, hints | `server/skills/shell.py` |
| Trace storage | `server/db/agent_runs_store.py` |
| `/api/projects`, `/api/runs` | `server/api_v2.py` |
| Turn binding on the endpoints | `server/main.py` |
| Trace UI | `clients/gajala/lib/widgets/run_trace.dart` |
| Projects list | `clients/gajala/lib/screens/projects_screen.dart` |
