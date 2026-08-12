# Project Context — Code-as-a-chat

> UNIVERSAL CONTEXT for all AI agents (Claude, Codex, Gemini).
> This file is mirrored to AGENTS.md, CLAUDE.md, and GEMINI.md — keep it as the
> single source of truth for what this project is and where it stands.
>
> AGENTS: when you make a meaningful change to this project, UPDATE the
> "Current State" and "Changelog" sections below before you finish.

## Overview
Code-as-a-Chat turns an always-on Mac into a headless personal dev server you
control from your phone in natural language. A FastAPI orchestrator exposes
"skills" (run Claude/Codex/Gemini CLIs on projects, notes, diary, reminders,
system stats, ports, sessions, Mac control, etc.); an LLM `shell` agent
routes/chains them and formats replies for mobile, streaming live progress
for long turns. Clients are a Telegram bot and a native Flutter Android app
("Gajala"). Local-first: data stays in `~/.codeasachat/`; only the AI CLIs the
user already uses talk to the outside.

## Tech Stack
- **Server:** Python 3.11+, FastAPI, Uvicorn, Pydantic, httpx, psutil,
  python-telegram-bot, python-dotenv. Push: google-auth (FCM HTTP v1).
- **Storage:** SQLite files in `~/.codeasachat/` (notes, diary, reminders,
  conversation memory, devices) + `state.json`, `api_token`.
- **App:** Flutter (Riverpod, dio, flutter_secure_storage, google_fonts,
  firebase_messaging, flutter_local_notifications). Android + web targets.
- **Ops:** macOS launchd (`scripts/codechat`), Tailscale Serve for phone access.

## Architecture / Key Files
- `server/main.py` — FastAPI app, lifespan, mounts `api_v2` router + scheduler;
  `/run` (single-response) and `/run/stream` (NDJSON step-by-step) entrypoints.
- `server/api_v2.py` — structured `/api/*` JSON backend for the app.
- `server/orchestrator.py`, `server/skills/` — routing path; skills self-register
  from a manifest (`claude_code.py`, `codex.py`, `antigravity.py` wrap the three
  CLIs via `cli_base.py`). `shell.py` is the LLM agent loop (routing + tool
  chaining), with an optional `on_event` callback for live progress.
- `server/scheduler.py` — background reminders + (optional) battery alerts.
- `server/notify.py` (Telegram push) / `server/fcm.py` (FCM push to the app).
- `server/db/` — SQLite stores. `server/config.py` — all env config.
- `clients/telegram_bot.py` — Telegram client. `clients/gajala/` — Flutter app
  (dashboard, chat, notes/diary/reminders/projects/usage/mac/system screens).
- `scripts/codechat` — launchd install/start/stop/logs.

## Conventions
- Match surrounding style; concise module docstrings explaining the "why".
- Skills subclass `Skill`; set `expose_to_agent`/`passthrough`/`final_output`
  flags rather than editing the router. New skills need no wiring.
- Secrets never committed: `.env`, `*service-account*.json`, `google-services.json`
  are gitignored. Server auto-generates `~/.codeasachat/api_token`.
- Auth: `/run`, `/run/stream`, and `/api/*` require `X-API-Token`; `/health` is open.
- After changing server code, restart cleanly (bootout + pkill + bootstrap) to
  avoid duplicate-uvicorn races.

## Current State
Working: full skill set (CLI runners, notes, diary, reminders, projects, ports,
sessions browser, filemanager, sysmon, usage, Mac control incl. Bluetooth +
wake/unlock). Shell agent with one-retry resilience + partial-result fallback,
plus an **OpenAI backup brain** (`SHELL_LLM_PROVIDER` auto|claude|openai) so it
keeps working when Claude usage runs out. Live-progress streaming over
`/run/stream`. **One pinned coding agent + one active native thread per project**
for claude/codex/gemini, with cross-model `.md` sync. Agent choice is stored per
workspace; Gajala resolves the newest thread from each CLI's own session store,
fallback models stay in that thread, and `scripts/project-agent` opens the same
agent/session interactively on the Mac (`SESSION_FOLLOW_NATIVE`); `general` home
base is the default workspace;
**per-directory app conversations** whose thread follows the active project
everywhere (incl. agent-driven switches) with confirm-to-move; pinned coding
engine **+ per-engine model** (claude opus/sonnet/haiku, codex gpt-5.6-sol/…,
gemini 2.5-pro/flash), switchable from the app or in chat ("switch to opus").
**Images** send + receive (screenshots to the agent, images back).
Telegram bot; Flutter app "Gajala" (light/dark) with FCM push end-to-end and
**15-second heartbeats on long `/run/stream` turns** so silent CLI work does not
lose the phone's HTTPS stream through an idle connection;
**shell tool-action compatibility** so model replies that put a registered tool
name directly in `action` execute it instead of surfacing an unknown-action error;
**10-minute coding turns** for Claude/Codex/Gemini so active build/deploy work
is not killed by the former generic 5-minute CLI timeout;
**polished, adaptive Android home-screen widgets** using launcher-safe
`RemoteViews` elements: a two-row Mac Mini remote
(Lock / Wake / Ask / Brain-dump) and a full Codaur widget with four provider
rows, quota meters, dedicated refresh, and local Qwen tokens. Ollama's exact
prompt/generated counts are persisted without prompt/reply content and exposed
through Codaur. **Codaur usage refreshes every 30 seconds while visible**, bypasses
caches, drops expired quota snapshots instead of presenting stale limits, and
avoids overlapping CLI requests when the screen first opens.
Night Shift jobs now use **versioned work orders** (outcome, approved plan,
policy, acceptance, phone-test handoff, and scope), explicit dependencies, and
recoverable Close/Reopen with history instead of permanent deletion.
Dependencies remain satisfied after a shipped/completed prerequisite is
archived, including repeated Close/Reopen cycles; collapsed task cards show IDs.
Rough queue utterances are saved as visibly distinct, non-runnable **Drafts**;
a Claude Sonnet **Refine** action expands them into complete work orders,
preserves the original wording + assumptions, and returns them held for review.
Gajala requires per-refinement confirmation and sends only the rough text—never
repo files or paths—to Claude.
That confirmation accepts optional owner instructions, and every active job has
a full manual editor for source text, type, structured fields, dependencies,
project, and engine; saving always returns it held for review.
Each active job also has a direct engine picker: Auto lets the queue orchestrator
choose from live quota/availability, while Claude/Codex/Gemini pin the job.
Refinement classifies coding vs research: research jobs bypass Git and return a
sourced, read-only report (`completed`) without contacting anyone.
Coding jobs run in managed, isolated Git worktrees, so your active checkout may
have uncommitted work without blocking the queue; Night Shift never switches,
cleans, or stashes your live files.
`auto` now authorizes the complete lifecycle: successful coding jobs merge,
deploy, restart when needed, verify localhost + authenticated APIs + the actual
Tailscale endpoint, and push without waiting for the owner. A durable deployment
ledger serializes releases and exposes `merging/restarting/verifying/live` or a
precise failure state. Rollback uses `git reset --keep` only when the expected
deployed commit is still current, preserving unrelated owner work; true
same-file overlap is the narrow case that stops for human resolution. The fix
agent uses the same coordinator and an isolated repair worktree.
An always-on **Queue Supervisor** now audits work every minute, independently of
the overnight build window. It recovers orphaned workers, classifies failures,
rotates recoverable retries across installed engines with a three-attempt budget,
and rebuilds failed Auto deployments against the live base. Only exhausted
recovery or genuine decisions escalate. Every task exposes `Why paused`, `Next
action`, attempts, and retry timing; Gajala refreshes Tasks every 20 seconds and
shows working, recovering, held, and genuinely blocked counts.
Deployed on a Mac Mini via launchd + Tailscale. Battery alerts disabled on this
always-plugged host. **Self-healing `fix` agent** (codex-powered): describe a
bug from the phone → it diagnoses + makes a minimal fix on a branch, auto-builds
app-side fixes (`build` skill → APK link) and gates server changes for your OK;
`/fix ship` merges, and a detached `restart_guard` health-checks + auto-rolls-back
so a bad server change can't lock you out.
**Night Shift** (overnight autonomous build queue, opt-in via `NIGHT_SHIFT_ENABLED`):
queue coding tasks from the phone (`/queue add [auto|mine] [engine] <project>: <task>`);
while inside the night window the runner keeps one job per engine in flight so all
three subscriptions build in parallel on isolated `night/*` branches, quota-gated
by the live codaur read (an engine at/over `NIGHT_QUOTA_STOP_PCT` sits out until its
window resets). `auto` changes continue through merge, deploy, verification, and
push; `mine` changes stage for `/queue ship`; decision-heavy tasks self-flag
`needs_you`. A morning FCM/Telegram report summarizes what
was built, what needs shipping, and tokens spent per engine. Four brakes: the
window, `NIGHT_MAX_JOBS`, an optional `NIGHT_TOKEN_BUDGET`, and the per-job timeout;
a per-repo lock keeps parallel same-repo jobs from colliding.
Gajala drives it from a **bottom-nav shell (Home / Tasks / Alerts)**: the **Tasks**
tab splits Active/Closed work, opens the complete work order, and provides
run-now / stop / ship / close / reopen / retag actions, plus an in-app Night
Shift toggle + window/max-jobs. When a job needs
a decision it pushes a **question** to the phone (status `awaiting_input`); you
answer inline in the **Alerts** inbox and the job re-runs with your answer appended.
Every inbox-worthy event (needs-input, job deployed/failed, night report, "new
build ready", fired reminders) is logged **and** pushed through one helper
(`server/notifier.py` + `notifications_store`) so the Alerts tab (with an unread
badge) and the FCM push always agree. Endpoints: `/api/queue*`, `/api/notifications*`.
**Dynamic skills marketplace**: every registered skill can be toggled enabled/disabled
from the Gajala app (overflow menu → "Skills marketplace") without a code deploy;
the shell agent and orchestrator immediately exclude disabled skills from routing,
so they become unavailable to the LLM agent on the next turn.

## Changelog (most recent first)
- 2026-08-11 — **Always-on Queue Supervisor and phone-visible queue health.**
  Queue monitoring is no longer tied to the Night Shift execution window.
  Recoverable worker loss, timeout, CLI failure, merge conflict, and safe rollback
  outcomes retry automatically on another installed engine with durable attempt
  and backoff metadata. `/api/queue` now returns queue health plus per-task
  blocker/next-action explanations; Gajala refreshes every 20 seconds and can
  nudge an audit. Also repaired an existing notes-provider app compile break.
- 2026-08-09 — **Autonomous safe deployment coordinator.** Queue and fix-agent
  shipping now share one durable, serialized deployment transaction. Automatic
  jobs merge and deploy end-to-end, with branch/overlap preflight, idempotent
  launchd restart, localhost + authenticated API + Tailscale verification,
  push-on-health, safe rollback that preserves unrelated edits, durable status,
  and Gajala's new `deploying safely` state. Fix attempts no longer require a
  clean live checkout because they run in their own managed worktree.
- 2026-08-09 — **Dynamic skills marketplace.** Every registered skill can now be
  toggled enabled/disabled from the Gajala app (overflow menu → “Skills marketplace”),
  persisted in `state.json`, without any code deploy or server restart. New
  `server/prefs.py` functions `is_skill_enabled()` and `set_skill_enabled()` manage
  state; shell agent's `_build_tool_catalog()` and `DELEGATE_SKILLS` filter disabled
  skills so they never appear in the routing model's tool list; `orchestrator.route()`
  checks enabled state before running a skill; `/skills` endpoint now includes an
  `enabled` flag per skill; new `/skills/{name}` POST toggles state. Gajala adds a
  `SkillsScreen` (reachable from dashboard overflow) listing all skills with toggle
  switches, and calls the new API method `GajalaApi.toggleSkill()`. Default: all
  existing skills are enabled (opt-in behavior unchanged). Verified: GET /skills
  returns accurate enabled status; POST /skills/{name} toggles it immediately;
  disabled skills are excluded from the agent's tool catalog; re-enabling restores them.
- 2026-08-09 — **Dirty live repos no longer block queued coding jobs.** Night
  Shift now runs each coding task in a clean managed worktree and keeps the
  resulting `night/<id>-...` branch while removing the temporary checkout. The
  owner's branch and uncommitted files remain untouched; app-only jobs build
  their APK from the isolated source tree as well.
- 2026-08-09 — Fixed Night Shift's false `waiting for #8` block. #8 had shipped
  successfully, but two Close/Reopen archive cycles left its current state
  `closed`/previous `held`; dependency checks ignored the durable earlier
  `shipped` history. Shipped/completed work now remains dependency-satisfying
  after archival. Gajala also shows `#ID` prominently on every collapsed task
  card and marks satisfied dependencies in detail.
- 2026-08-08 — **Direct per-job engine picker.** Task actions now expose Auto,
  Claude, Codex, and Gemini without opening the full editor. Auto clears any
  previous runner and lets the queue orchestrator choose using configured
  engines plus live quota/availability; explicit choices pin the job. Matching
  API and `/queue engine <id> ...` commands reject running/closed jobs safely.
- 2026-08-07 — **Refinement guidance + editable work orders.** Claude's consent
  dialog now accepts optional per-run instructions (for example, “research only”
  or “keep the existing API”). Active jobs gain a full Edit work order sheet for
  source text, coding/research type, outcome/context/plan/policy/acceptance,
  phone handoff, assumptions, scope, dependencies, project, and engine. The API
  validates IDs/type/engine, regenerates the worker prompt, records manual
  authorship, and safely returns every edit held for review.
- 2026-08-07 — Fixed queue #7's repeat failure: Gajala had hidden Refine once a
  job already said Refined, so the old Qwen classification could only be run
  again. Refine/Re-refine is now available for every non-running active job.
  Migration also recognizes unmistakable pre-work-type Qwen research specs and
  routes them through the read-only research pipeline instead of Git.
- 2026-08-07 — **Draft → Refine queue workflow.** Natural, incomplete queue
  captures are now Drafts and are forced held: workers cannot claim/run them or
  mark them automatic. `/queue refine <id>` and `POST /api/queue/{id}/refine`
  use Claude Sonnet to classify and create a complete work order from only the
  rough capture (no repo files/paths), preserving source and assumptions; the
  result stays held for owner review. Gajala distinguishes Draft/Refined cards,
  adds quick capture and one-tap Refine, and still supports manual structured
  work orders. Existing v1 rows migrate conservatively by completeness.
- 2026-08-07 — **Research queue execution + explicit failure reasons.** Refine
  now classifies coding versus research. Research jobs bypass the Git/branch
  pipeline, run as strictly read-only investigations, store their sourced report
  as `completed`, and never contact or mutate external systems. Failed cards,
  details, and notifications label the exact reason. Queue #7's old coding run
  failed because `Projects/general` is not a Git repository; its next confirmed
  refinement classifies it as research.
- 2026-08-07 — **Durable Night Shift work orders.** Queue rows now carry a
  versioned structured spec plus explicit job dependencies; workers claim a job
  only after every prerequisite is shipped. Legacy free-text rows migrate
  without losing their original task. Permanent deletion was removed from the
  queue API, agent command, and Gajala: closing requires a reason, keeps history,
  and reopening restores the item as held. Gajala adds Active/Closed tabs,
  complete expandable detail, dependency state, and a structured composer that
  requires outcome, plan, acceptance, and phone testing for automatic work.
- 2026-08-07 — **Gajala cockpit for Night Shift + a notifications inbox.** New
  `notifications_store` + `server/notifier.py` (one call logs an inbox row AND
  pushes, so the app's Alerts tab and FCM never drift); reminders + night events
  route through it. Human-in-the-loop: a night job that hits a decision now goes
  `awaiting_input` and pushes a `queue_input` question — answering (`/api/
  notifications/{id}/respond`) appends your answer to the task and re-runs it.
  `night_shift` gains `run_now` (dispatch any time, even outside the window),
  `stop_job` (kill the build, park `stopped`), and runtime settings via
  `prefs.night_settings()` (in-app toggle/window/max-jobs, no .env edit). New
  `/api/queue*` + `/api/notifications*` endpoints. App: a bottom-nav shell
  (Home/Tasks/Alerts), `tasks_screen.dart`, `notifications_screen.dart`, models +
  providers + FCM routing for the new push types.
- 2026-08-07 — Fixed the post-Qwen Gajala `HttpException`/permanent
  “Reconnecting…” regression. `/run/stream` now emits an immediate body frame,
  beating the phone/proxy's 15-second connection race while local inference is
  silent; recovery polls immediately and describes a real interruption instead
  of claiming it is reconnecting. Concurrent screen/dashboard/widget Codaur
  refreshes are coalesced with a bounded last-good fallback, eliminating the
  intermittent `/api/usage` 502s recorded by the app.
- 2026-08-07 — **Night Shift: overnight autonomous build queue (#52).** New
  `server/night_shift.py` runner (its own lifespan task) claims queued jobs and
  builds them overnight across claude/codex/gemini in parallel, one per engine,
  quota-gated via the codaur usage read. `server/db/night_queue_store.py` is the
  durable job list with an atomic `claim_next` (BEGIN IMMEDIATE) so parallel
  workers never double-claim; `server/night_exec.py` runs each job isolated (no
  session pointer, no resume → never forks your phone threads) reusing each CLI's
  own token parser. The `queue` skill drives it from chat/app (add/list/review/
  ship/drop/tag/backlog/status). Every job runs on a throwaway `night/*` branch;
  app-only changes to this repo auto-deploy the APK, server/other changes stage
  for `/queue ship` (server ships still go through `restart_guard`). Queue-empty +
  quota-left pulls from per-project `~/.codeasachat/backlogs/<name>.md`. Off by
  default; config `NIGHT_*` in `.env.example`.
- 2026-08-07 — Reworked Codaur into a dense midnight telemetry panel after
  launcher testing showed too much empty space. Removed weighted row stretching,
  introduced compact fixed-height provider strips, higher-contrast token/quota
  hierarchy, neon provider accents, and explicit live-sync labeling.
- 2026-08-07 — Fixed Codaur's launcher-level “Could not load widget” failure.
  The redesigned provider dots used plain Android `View` elements, which are
  rejected inside `RemoteViews`; they now use supported `ImageView` elements.
  Added a smoke test that rejects unsupported tags in every widget layout.
- 2026-08-06 — Added exact local Qwen/Ollama usage end to end. Every Qwen
  response records only its metrics envelope (input/generated tokens, model,
  project, source, duration) in `qwen_usage.jsonl`; prompts and replies are not
  stored. Codaur 0.2.0 consumes it as a first-class no-quota provider. Rebuilt
  both Android widgets as launcher-adaptive application widgets: Codaur now has
  four structured provider rows, quota meters, timestamp and separate open/
  refresh actions; Gajala Remote now has a clear status header and roomy 2×2
  Mac/Ask/Brain-dump controls.
- 2026-08-06 — Completed the one-project/one-agent/session contract. Coding-agent
  choice is now per workspace instead of one global toggle; fallback models
  resume the existing thread and only create a replacement when the CLI
  explicitly says the saved session is invalid; the active-sessions API
  reconciles native stores immediately; and `scripts/project-agent [path]`
  resumes the pinned project thread from a Mac terminal. Coding CLI attempts now
  write source + token counts to `~/.codeasachat/cli_runs.db`, enabling exact
  Gajala-origin monitoring for new turns.
- 2026-08-06 — Hardened recent Gajala project workflows: all three coding CLIs
  now get 10-minute turns; `/files` resolves relative paths against the active
  workspace and recognizes bare sibling-project names; new-project "idea passed
  / build it" handoffs deterministically read project context then use the
  pinned coding agent; short repeat-update requests return the actual last reply;
  identical timed-out tool calls are not launched twice; and Qwen uses Ollama
  JSON mode for structured shell/notes/reminder decisions.
- 2026-08-06 — **One session per (project, engine), shared with the Mac.** Session
  reuse previously trusted only `cli_sessions.db`, a server-side pointer that
  only server-driven turns ever wrote — so running `claude`/`codex`/`gemini`
  yourself in a project opened a session Gajala never saw, and the next app turn
  resumed the older id and forked the thread. New `server/db/native_sessions.py`
  reads each CLI's own store (claude `~/.claude/projects/*/<id>.jsonl` matched on
  the in-file `cwd`, since the dir encoding is version-unstable; codex
  `session_meta.payload`; gemini `projectHash` = sha256 of the path) and returns
  the newest session for a folder. `cli_base._resume_id()` now resumes that,
  falling back to the stored pointer only when discovery finds nothing. Backup-
  model retries now remain on that same thread; a fresh thread is created only
  when the CLI explicitly rejects the stored session. Costs 4–13 ms per run.
  Escape hatch: `SESSION_FOLLOW_NATIVE=0`.
- 2026-08-02 — **Qwen as a first-class model + the default Gajala brain.** `_haiku`
  now routes through a per-task provider chain (`QWEN_TASKS`, default
  `shell,notes,diary`): those run on local Qwen first with Claude+OpenAI as
  fallback; other tasks (reminders) keep Qwen as the LAST fallback so nothing dies
  when Claude+OpenAI are exhausted. A `validate` callback escalates unparseable
  Qwen JSON to Claude automatically (graceful). Qwen is a switchable engine like
  claude/codex/gemini (`prefs` + `/api/model` + the `model` skill + the app's
  engine chips, now rendered from the API's options); its `_engine_hint` is honest
  (local chat/reasoning, no file access — use claude/codex for file work). Config:
  `OLLAMA_URL`, `QWEN_MODEL`, `QWEN_TASKS`.
- 2026-08-02 — Local **`qwen`** chat skill: talk directly to a local Qwen2.5-7B
  via Ollama (localhost:11434) — no quota, fully offline. Conversational (replays
  session history into Ollama's chat API so it follows context). Also a **`auth`**
  skill: check each coding CLI's login and re-authenticate **Codex from the phone**
  via its device-code flow (surfaces the one-time code; the user approves in their
  own browser — passwords never touch the server). Shell agent now stops
  **parroting stale errors** (a past "OAuth expired" in history is not the current
  state — it re-calls the tool and, on any auth/401 failure, routes to `auth`).
  Dashboard gains icons/colours for both.
- 2026-07-29 — Fixed shell errors such as `unknown agent action: 'filemanager'`
  when the routing model emitted a registered tool name directly as its action;
  the shell now normalizes that valid shorthand into a normal tool call.
- 2026-07-22 — Raised Codex's subprocess allowance from the generic 5 minutes
  to 10 minutes after a healthy portfolio logo build/deploy was killed while
  Sites was still publishing. Other CLI skill timeouts remain unchanged.
- 2026-07-19 — Fixed "Gajala typing…" stuck forever on long turns: the reply was
  completing + persisting server-side, but a dropped mobile/Tailscale stream left
  the app hanging (and the FCM ping is suppressed while foregrounded). The app now
  recovers — on an abnormal stream end it polls chat history for the persisted
  reply and shows it. Also tightened the fix agent's repair prompt: trace the exact
  symptom's code path, and be honest that UI/behavioural fixes are UNVERIFIED
  hypotheses (say how to confirm) instead of implying they're fixed.
- 2026-07-19 — In-app updater: the build skill stamps a fresh versionCode via
  `--build-number=<epoch>` (no pubspec churn) and writes `apk_version.json`;
  `GET /api/appversion` serves it; the app compares its own buildNumber and shows
  an "Update available" banner on the dashboard that downloads the APK and
  launches the system installer (open_filex, REQUEST_INSTALL_PACKAGES). Closes
  the self-heal loop on the phone — no more copy-paste APK link.
- 2026-07-19 — Added 15-second NDJSON heartbeats to `/run/stream`; long silent
  coding/repair calls now keep the HTTPS response alive instead of surfacing
  Dart's `Connection closed while receiving data` error.
- 2026-07-18 — Fixed an intermittent Codaur 502 on screen open: the provider's
  initial load and a redundant post-frame refresh were launching overlapping
  Codaur CLI requests. The normal initial load now runs once; manual, resume,
  and 30-second refreshes are unchanged.
- 2026-07-17 — Per-engine BACKUP model: each engine (claude/codex/gemini) can
  have a fallback model; if a coding run fails on the primary, `cli_base` retries
  once on the backup with a fresh session (codex `_failed` also catches a
  turn.failed that exits 0 — e.g. model version skew). Stored in prefs
  (`coding_backup_models`), set via `/api/model` (`backup` field), the `model`
  skill ("codex backup gpt-5.5"), or the app's context sheet. Defaults set:
  claude opus→sonnet, codex gpt-5.6-sol→gpt-5.5, gemini→2.5-flash.
- 2026-07-17 — Self-healing fix agent (MVP): `fix` skill runs codex on this repo
  to diagnose + minimally fix reported bugs, gated by a git diff — app-only
  changes auto-build via the new `build` skill (rebuild + deploy APK), server
  changes are quarantined on a branch until `/fix ship`. `restart_guard` runs
  detached so server restarts health-check + auto-roll-back (no lockout).
  Guardrails: one bounded codex run (FIX_TIMEOUT), triage prompt that stops +
  asks on non-code / out-of-scope issues.
- 2026-07-16 — Per-engine model switching: pick the model within each engine
  (claude opus/sonnet/haiku, codex gpt-5.6-sol/terra/…, gemini 2.5-pro/flash).
  `prefs` stores a model per engine (codex presets read live from its model
  cache); CLI wrappers pass `--model`; `/api/model` GET/POST carries models +
  presets; new `model` skill lets the shell agent actually switch from chat
  ("switch to opus") instead of hallucinating it; app context sheet gains a
  per-engine model picker.
- 2026-07-15 — Fixed stale Codaur usage end-to-end: repaired Claude's status-line
  capture path after the Desktop → Projects migration, added foreground polling
  every 30 seconds, cache-busting/no-store responses, expired-window filtering,
  and a generic limit fallback for the Android usage widget.
- 2026-07-13 — Android home-screen widgets via `home_widget` (Gajala quick
  actions: Lock / Wake / Ask / Brain-dump; Codaur usage glance) + remote mac
  **wake/unlock** action (`caffeinate -u`). Honest limit: macOS blocks typing a
  password into a truly-locked screen, so "unlock" = wake (real unlock only when
  no password is required after sleep / within the grace window).
- 2026-07-13 — Backup brain for the shell agent: `_haiku` (used by
  shell/notes/diary/reminders) falls back to the OpenAI API when Claude fails,
  via `SHELL_LLM_PROVIDER` (auto | claude | openai) + `OPENAI_SHELL_MODEL`.
  Keeps the assistant working when Claude usage runs out.
- 2026-07-09 — App chat follows the active project everywhere: server reports
  `workspace` after each turn so the header + thread key stay synced even when an
  agent switches projects mid-turn; skill tabs now persist + get per-engine
  threads; reliable scroll-to-latest. Codaur/usage screen auto-refreshes on open
  + app-resume.
- 2026-07-05 — Per-directory conversations + confirm-to-move; `general` home base
  as the default workspace; shared-context (`.md`) pre-run sync guard.
- 2026-07-04 — Session reuse per (workspace, engine) for all three engines
  (claude/codex/gemini) with cross-model `.md` sync; chat `dir · model` header +
  switcher; pinned coding engine. Image send + receive (upload → agent, images
  back). Repo story-log under `docs/story/`.
- 2026-07-04 — Live progress for long agent turns: `/run/stream` NDJSON
  endpoint, `shell.py` on_event callback, app renders a live status bubble
  instead of a silent wait; chat-reply completion pushes to Gajala as a
  fallback if the stream drops.
- 2026-07-01 — FCM push (app+server) end-to-end; Bluetooth device control; shell
  agent retry/partial-result; open-source prep (LICENSE, README, .env.example,
  app moved to clients/gajala, removed old Expo client + internal docs).
- 2026-06-08 01:42 — context initialized

## TODO / Next Steps
- Night Shift follow-ups (#52 core landed): schedule heavy jobs right after a
  Codex 5h reset, per-job retry/backoff, a test-gate before app auto-deploy, job
  dependency chaining, and a Gajala "Night Shift" screen over a `/api/queue`
  endpoint (today it's driven through the `queue` skill / chat).
- Push from more places (note/diary nudges).
- CI for the Flutter app; publish app as needed.
- Rotate any credentials shared during setup.
- At-rest security: FileVault is OFF and `.env` is world-readable — consider
  enabling FileVault (mind the headless unlock caveat) and `chmod 600 .env` +
  the SQLite stores.
- Optional widget follow-ups: Sleep tile, Wake-on-LAN (wake from full sleep),
  light-theme widget styling.
