# Confidence-gated command execution

Gajala now attempts a small validated command route before context retrieval,
continuity review and the model loop. A unique whole-command grammar match must
map to a reviewed executor, satisfy argument/target checks and score strictly
above FAST_COMMAND_CONFIDENCE (default 0.80). Everything else uses the existing
assistant. This router uses conservative policy scores, not a trained model's
calibrated probability: 0.97 for catalog-backed/closed commands, 0.88 for bounded
speech/URL/music queries, and 0.86 for navigation. The trial has insufficient
labels to estimate empirical accuracy at these values.

Supported fast execution: named home lights/fans and dimming; room lights/fans;
Mac lock, wake, sleep, screen/webcam capture, speech, notifications, HTTP(S) URL,
Bluetooth power/list and verified volume; streamed-phone playback, volume keys,
alarms/timers, app opening, flashlight, DND, settings, camera, navigation and
notification reads. Existing phone permissions still apply. Numeric phone volume,
unknown apps, calls, messages, garage, contextual replies, mixed/conditional or
unresolved commands retain the regular path. Calls never bypass confirmation.

Home targets are built from Gogon's live GET catalog (15-second classification
cache, 750ms discovery budget). Execution independently checks online status,
resolves targets and verifies outcomes through the existing home driver. Room
operations explicitly carry lights/fans. A light cannot become a fan during
regular or fast recovery. Unknown category metadata blocks exact mutation.
The production grammar is checked in; editing the experimental lab registry
cannot authorize production execution.

Fast actions use the same durable operation receipts as normal tools, receive
original request IDs, emit run/step/result/completion frames and retain chat
history. Their trace names hassil, capability and score. Failed/unverified
execution returns its actual result; it never falls through to another executor.
The fallback boundary is before execution only. Existing cancellation propagates.

Set FAST_COMMANDS_ENABLED=0 to disable fast routing; target-category guards remain.
Set FAST_COMMAND_CONFIDENCE=0.90 to exclude free-text music/speech/navigation
from the fast path. Values below 0.80 or above 1 disable candidate selection;
comparison is strict, so 0.97 excludes 0.97 candidates. Restart after env changes.

Validation: 401 server tests pass, including scoring boundaries, live-stream
requirements, default music player, room subsets, wrong-category blocking,
normal fallback, and no model retry after attempted execution. Test operations
are isolated from the owner's durable operation store. Read-only replay of 67
captured requests produced 19 fast candidates against the current live catalog;
this is retrospective coverage, not accuracy or proven device latency. No real
physical action was triggered for validation.
