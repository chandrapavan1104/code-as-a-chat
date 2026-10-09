# Gajala: chat usability and visible work outcomes

October 8, 2026. Implemented core delivery slices A–D after owner approval.

Implemented: swipe replies with an accessible Reply fallback for fenced-code messages,
responsive four-line composer, Chats/Work/Library/Alerts navigation, consolidated Settings,
individual unread alerts, Work result pages, immutable downloadable reports, per-attempt
bounded/redacted diagnostics with 30-day/20-attempt retention, repository preflight failure
classification, honest legacy failure display, idempotent queue creation, exact notification
links including cold launch, and receipt-guarded results replying to the origin chat message.
The existing in-app updater remains visible across the new destinations.

Validation: 291 server tests and 139 Flutter tests pass; three final notification routing tests
also pass. Signed APK build 1791504833. Analyzer has no errors (existing voice/style warnings
remain). Physical-phone gesture, screen, offline/reconnect and device-action acceptance remains.
Historical discarded CLI logs cannot be reconstructed. General offline cache presentation
across all Library screens is a follow-up; this release preserves existing offline chat outbox
and voice behavior rather than introducing a new app-wide cache layer.

## Audit scope and evidence

Reviewed the latest implementation in the voice-handsfree worktree (792d446c), chat and voice entry points, composer/content widgets, navigation, dashboard, Tasks/Alerts, phone abilities, shared themes, supporting feature screens, queue storage/API, execution and supervisor paths. Read local queue metadata without running or retrying jobs. This is a source/data audit, not a pixel or end-to-end device walkthrough. Physical-phone acceptance remains necessary.

Concrete findings:

- `chat_screen.dart:1167` wraps selectable content in a long-press Reply gesture. Text selection and Reply compete. The user's observation matches the structure.
- The composer has separate attachment and mic buttons, a six-line text field, and Send (`chat_screen.dart:810`). These controls constrain available text width. The long default and busy-state hints can wrap; theme input padding also contributes height. The exact phone geometry needs reproduction.
- Sending during a turn becomes “Queue message” with a playlist icon, which is ambiguous beside the separate Tasks queue.
- Tasks has only Active/Closed. Completed and shipped work remains mixed with active work; queue health and Night Shift configuration precede the jobs.
- Task summaries are two-line previews; the full report is buried in the detail sheet. Research output exists: three closed reports contain 1,106–15,294 characters.
- Coding execution retains a parsed summary, branch, changed files and deployment evidence. Raw CLI stdout/stderr are discarded. CLI run records cannot reconstruct those diagnostics.
- The jobs schema lacks artifact, per-attempt output/log, CLI session and originating chat message references. Notifications identify a job, without a reply link to its original chat.
- The local queue contains 26 jobs: 9 shipped, 12 closed, 2 held, 1 awaiting input, 1 failed, 1 needs_you; none queued/running. Closed jobs are not a failure-rate denominator.
- Jobs #23/#24 failed repository preflight before the CLI ran (11–13ms). #23 is Mine, skipped by automatic recovery, yet retains “Worker is implementing and testing” as its next action and no failure classification. #24 stopped for a non-retryable cause at 2/3 attempts; its message implies attempt exhaustion. Both recorded attempts used Claude. A currently valid repository does not establish its state at the historical failure.
- Opening Alerts marks every item read. This conflicts with its explicit Mark all read action and can hide unattended decisions.
- Phone abilities says several actions “always work,” including music. That copy exceeds observed device evidence. App-wide settings are distributed across Home, voice settings and queue controls.

## Product direction

Make the conversation the main workspace and make a finished result the main unit of background work. Preserve the current execution engines, worktrees, approvals, scope rules, and durable storage. Refactor their presentation and missing contracts incrementally; a framework rewrite is not justified by these findings.

### 1. Comfortable chat first

Swipe right on either message bubble to reply. Show a reply icon, trigger a single haptic at a deliberate drag threshold, and spring back on release. Keep long press for selection/copy. Provide a menu/semantic Reply action for accessibility. Vertical scrolling, selected text, horizontal code scrolling and file controls must retain their gestures. Use a custom bounded gesture rather than deletion-oriented Dismissible behavior. Do not restrict swipe to an undiscoverable narrow gutter.

Replace the composer with one coherent rounded surface:

- One + attachment entry, a wide editor with the short hint “Message…”, and a trailing action.
- Mic when empty; Send when a draft exists. Keep dictation accessible through the attachment/action menu while editing. Distinguish dictation from the full voice conversation entry in the header.
- A one-line empty height around 48–52dp; grow only for entered text, up to a bounded editor height, then scroll inside. Preserve 48dp touch targets, keyboard safe areas and large-text usability.
- Quote and attachment previews sit in a compact tray above the editor, with visible removal controls. Preserve drafts/reply targets on navigation or connection failure.
- During another turn, say “Send follow-up”; show whether it will steer existing work or wait its turn. Do not call this a queue task.
- Keep project/model choices in one context sheet; show the current project unobtrusively. Keep technical traces collapsed.

Comparison: Signal explicitly supports swiping right to quote and tapping the quote to navigate. Telegram uses a growing editor with controls positioned around the editing surface; its six-line limit shows that maxLines alone is not the defect. Material guidance supports a multiline editor starting at one line and expanding for content.

### 2. Work that shows what it produced

Rename the user-facing Tasks destination to Work. Use Running, Needs you, Results and All filters; keep drafts/closed work available in All. Count actionable work accurately. Move Night Shift schedule, engine policy and supervisor controls into Work settings.

Each compact card should answer: what was requested, what is happening, what was produced, and whether the owner needs to act. Present one primary action: Open result, Answer, Continue, or Review change. Order by attention/activity rather than an opaque list of backend status strings.

A dedicated work detail page should open on the deliverable:

1. Result: rendered report, files, code/diff, APK/build link, or explicit partial result; Open/Copy/Download as applicable.
2. Outcome: short summary, validation evidence and remaining limitations. A coding summary does not prove deployment or phone acceptance.
3. Progress: durable attempt timeline and current stage.
4. Advanced: full retained output/logs, engine, project and deployment details.

Store a durable result record separately from a status summary. Proposed fields: job/attempt IDs, kind, title, content or authenticated file reference, MIME type, source/validation references, completeness and timestamps. Stage deliverables before worktree cleanup. Retain bounded stdout/stderr by attempt with clear truncation, secret filtering and a retention limit; no credentials in results. Preserve existing reports during migration; do not imply historical raw logs can be recovered.

Link new jobs to origin conversation/message/request IDs. Deliver one final result reply to the originating message, with the same result accessible from Work and its notification. Use a durable outbox/idempotency key so reconnect/restart cannot duplicate results. Legacy jobs get results without inventing an origin. Chat-launched background research and Night Shift may share result presentation while retaining their different execution policies.

### 3. Correct failure and recovery behavior

Fix classification and explanatory state before increasing retries:

- Validate coding repository/project identity at capture and immediately before execution. Research remains valid without Git. A general non-repository workspace should invite selection/creation of a repository for coding, not consume an engine attempt.
- Separate configuration/preflight failures from auth, quota, worker exit, timeout, merge, deployment and verification failures.
- Record each attempt's engine, stage, cause, timestamps, partial output and recovery decision. Render a specific cause plus one useful next action for Mine and Auto alike.
- Clear stale running explanations when a job becomes terminal. Say “Recovery needs a repository” for invalid_repo; say “Attempt limit reached” only at the configured limit.
- Rotate engines only where it can address the cause. Changing an LLM cannot repair a missing repository. Use backoff for transient failures and preserve non-retryable decisions without repeated token spend.
- On completion clear active blocker/failure fields while preserving historical failures in the attempt timeline.
- Reconcile old contradictory records with an explicit history event, not silent historical rewriting. Do not automatically restart the owner's failed jobs during migration.

### 4. Consistency across Gajala

Recommended navigation: Chats / Work / Library / Alerts. Chats opens the most recent conversation and exposes a conversation picker. Library groups notes/diary/reminders, files, projects and device controls. Keep useful Home quick actions as a compact optional panel rather than a mandatory intermediary.

One Settings entry groups connection, voice, phone permissions, appearance and advanced execution policy. Permission screens distinguish Enabled from Android permission granted from Tested/Available; remove “always works” claims. Voice actions retain the same conversation log and concise confirmation; make the chosen input mode explicit.

Alerts remain unread until individually opened or explicitly cleared. Job notifications open that job's result or decision. Keep settings/help failures readable, provide retry on failed data loads, retain cached content with an honest timestamp when offline, and apply the same result/empty/error styles across Library screens. Avoid changing diary persona or Mac/music execution semantics as part of a cosmetic pass.

## Delivery order and gates

| Slice | Scope | Release evidence |
|---|---|---|
| A | Swipe reply, composer, clear follow-up wording | Gesture/widget coverage; Android walkthrough at 320/360/412dp, large text, keyboard, quotes, images, code selection and scrolling |
| B | Durable results/attempt output, prominent Results, exact notification deep links | Existing research reports remain readable; new report/file/code results survive restart and worktree cleanup; auth and idempotency checks |
| C | Preflight and honest recovery state, old-record reconciliation | Invalid Git never launches CLI; Mine/Auto errors explain cause; quota/transient retry differs from configuration failure; attempt history survives retry |
| D | Chats-first navigation, Library and Settings consistency, Alerts read behavior | Tab/draft state preserved; individual unread behavior; offline/reconnect and large-text device acceptance |

Build a replay suite from the reported failures: swipe over selectable text, crowded empty composer, find an old report, inspect a preflight failure, answer a blocked job, receive a long background research result, open its notification, and resume after a restart. Unit tests establish contracts; phone walkthrough establishes ergonomics. Music/call/Mac actions require separate observed action acceptance, not a successful build badge.

Track result-open discoverability, missing deliverables, contradictory-state count, and failures by cause/attempt. Keep telemetry local and content-free. Ship small slices with a reversible migration/rollback path rather than one broad UI/backend release.

## Sources and code anchors

- [Signal: reply to a specific message](https://support.signal.org/hc/en-us/articles/6851465208986-Reply-to-a-specific-message)
- [Telegram Android composer source](https://github.com/DrKLO/Telegram/blob/master/TMessagesProj/src/main/java/org/telegram/ui/Components/ChatActivityEnterView.java)
- [Material text fields](https://m2.material.io/design/components/text-fields.html)
- Flutter: `chat_screen.dart:742–865,1167`, `chat_content.dart:89`, `tasks_screen.dart:34,459,1265`, `home_shell.dart:22`, `phone_abilities_screen.dart:94`, `core/theme.dart:119`.
- Server: `night_queue_store.py:51`, `night_shift.py:333,443,505`, `cli_runs_store.py:30`, `api_v2.py:861`, `queue_supervisor.py:59,194`.
