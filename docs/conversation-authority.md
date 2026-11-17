# Conversation authority, quick access, widget recovery — November 17

## Problem and behavior

App conversations previously shared a project+engine native session pointer that
followed whichever native session was newest. The shell reconstructed recent
history but did not automatically supply it to coding tools. Completion review
could discard explanations whose wording resembled an execution claim.

The new path binds native sessions to conversation + canonical workspace + engine.
There is no automatic adoption of ambiguous legacy pointers. Existing transcripts
and legacy pointers remain intact; the first conversation-bound coding request
starts fresh with its saved chat context unless an explicit session is attached.
`/sessions continue` refuses a native session owned by another conversation and
returns its owner conversation ID. Model changes within an engine keep its binding.
Legacy callers without a conversation keep the existing native-follow behavior.

`server/conversation.py` owns a bounded context package used by the router,
fallback providers, coding handoffs and optional completion review. It includes
recent messages, exact reply sources, a task snapshot and derived conversation
state. State is an **extractive** user-source summary, not a model-written claim
of what happened. Original source messages stay available for exact retrieval.
User constraints/corrections and decisions carry message IDs; observations carry
run IDs and structured tool results. Assistant prose never becomes verified fact.
Historical coverage, excerpts and omissions are explicit. A failed projection
falls back to raw history. Retrieval defaults to the current chat; cross-chat
retrieval remains an explicit context tool. Diary data is not added.

Interactive execution locks by conversation before inserting the user message.
A queued turn uses its accepted work snapshot. An older completion cannot overwrite
a newer task revision. Background outcomes append evidence rather than rewriting
user requirements. Locks are process-local: production must continue to use one
Uvicorn worker. Durable operation receipts remain the side-effect boundary.

Execution reports still require evidence; ordinary explanations do not need a new
tool call. A rejected composed reply retains its useful content with an explicit
verification note. Actual unobserved live/action claims remain unverified.

## Interfaces and rollback

Existing `session_id` is the conversation key. `/api/sessions/active` accepts optional
`session_id` and `project`; conversation-scoped discovery never reconciles newest
native pointers. `/run` also returns optional completion data. `/api/runs/{id}`
includes context metadata: version, source IDs, summary watermark, excerpt status,
binding and fallback reason. Metadata contains no duplicate prompt body.

Additive tables: `conversation_state` in conversations.db and
`conversation_cli_sessions` in cli_sessions.db; additive `runs.context` column.
Old messages, receipts and traces are not rewritten. Clear-chat also clears its
derived state. `CONVERSATION_CONTEXT_ENABLED=0` disables derived projection use
while retaining recent context and conversation-owned session isolation. It never
re-enables silent native-session following for conversation-bound requests.

## Phone UI and widgets

Quick access opens Terminal or Trading from Chats, Work, Library and Alerts.
Existing bottom tabs, Library entries, notification routes and security gates stay.
Chat's less-frequent New conversation / Review changes actions move to overflow
so the header remains usable on narrow screens.

The installed home_widget 0.9.3 worker uses APPEND on one work chain and finishes
before its Dart callback completes. Initialization failure can poison subsequent
appended jobs. This is a code-proven failure mechanism, not a claim that it was
the cause of the owner's transient failure (no phone logs were captured).
The app-owned worker awaits the Dart callback, coalesces repeated taps with KEEP,
and persists the WorkManager job ID before dispatch. A restarted job cannot repeat
a possibly executed action. Each new tap starts a new independent job after failure.
Workers never request automatic retry. A native 75-second bound reports interrupted
outcomes; an expired busy lease is shown as uncertain on the next widget render.

Codaur retains last successful data/capture time, marks refresh failures stale,
and hides expired quota meters on render. Its platform update tick is 30 minutes
(rendering only; Android schedules this best effort). Ask launch links wait for
navigation/config readiness and open the selected conversation. Settings has a
copyable 32-record local diagnostic journal containing stage, category and timing,
never credentials or raw transport error strings.

## Verification

Automated tests simulate providers, CLI tools, Mac actions and widget transport.
They cover context handoffs, exact replies, source-only summaries, projection
failure, session isolation, newer native sessions, engine bindings, bill followups,
ordinary explanations, turn serialization, revision protection, menu ergonomics,
refresh coalescing, missing config, malformed data, stale quota recovery and timeout
non-replay. Existing Terminal and Trading authorization tests remain required.
Physical-phone launcher/background execution remains an acceptance check.

Release validation: all 216 Flutter tests pass. The server suite's known
`test_notification_reply_approves_through_the_api` failure was reproduced before
this change on deployed #78 and is excluded explicitly; the trading guard is not
weakened. Final server counts, build and deployment evidence are recorded below.

Final verification: 540 server tests passed, 1 known baseline failure explicitly
excluded; 216 Flutter tests passed. Android release assembly passed, including the
new Kotlin worker and RemoteViews providers. Static analysis has no errors or
warnings in the new navigation/widget code (style info remains). APK SHA-256:
`f15f2c4f73cf74dbaaba29a7a08dff9a805b30b94b1f8ffaa548fc84a9ef0daf`.
