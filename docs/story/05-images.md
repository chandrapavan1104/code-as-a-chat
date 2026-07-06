# 05 — Images

## The Spark

Half of real debugging is *pointing at something*. "Why is this button
off-center?" "What's this error dialog?" On a laptop you'd screenshot and drop
it in. From the phone, Gajala was text-only — so the most natural way to explain
a visual problem was the one thing I couldn't do. And the reverse hurt too: I'd
ask for a screenshot of the Mac and get... a sentence describing it.

## The Problem

Images had to flow **both ways**:

- **Send:** attach a screenshot to a message and have the agent actually *see*
  it — read the error, spot the layout bug, then fix it.
- **Receive:** let a skill hand an image *back* into the chat — a real
  screenshot of the Mac screen, rendered inline, not a file path in prose.

## The Thinking

**Sending** turned out to lean on something already true: Gajala's Claude tool
can read images. The agent's prompt even understood an "[User sent an image,
saved at: …]" marker. So the missing piece wasn't intelligence — it was
*plumbing*: get the bytes from the phone to a path on the Mac, then let the
existing agent do what it already could. I skipped a heavyweight multipart
upload dependency and just posted raw bytes to a small `/api/upload` endpoint.

**Receiving** needed a convention and a guard:

- **The convention:** a skill signals an image by emitting an `[image: /path]`
  marker in its output. But there was a catch — Gajala rewrites tool output in
  her own voice, and might *drop* the marker. So the marker is collected
  **mechanically** at the agent layer and re-attached to the final reply, and
  the prompt tells her not to echo raw paths. Belt and suspenders.
- **The guard:** serving files back to a phone is a security surface. A naive
  "give me this path" endpoint is a path-traversal hole waiting for
  `/etc/passwd`. So every served path is checked against an allow-list — the
  uploads directory and the active workspace only, with symlinks and `..`
  resolved first. Anything outside 404s.

The satisfying part: the `mac screenshot` skill already existed, but only pushed
to Telegram. Redirecting it through the shared uploads directory turned it into
the first real *producer* of received images — "show me my screen" now renders
the actual screen, in the chat.

## What We Built

Pick a photo or screenshot from the phone, it uploads, and the agent reads it
via its Claude tool. Skills emit `[image:]` markers that the app strips and
renders through an authenticated, sandboxed `/api/file` endpoint — including when
you scroll back through history. `mac screenshot` now shows the Mac's screen
inline.

## The Payoff

The chat gained eyes in both directions. I can shove a screenshot at it and say
"fix this," and I can ask to *see* the Mac and actually see it. For coding from a
phone, that closes one of the biggest gaps — a huge amount of dev work is
visual, and now the conversation can be too.
