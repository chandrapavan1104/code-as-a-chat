# 04 — Live Streaming

## The Spark

The completion ping (story 03) fixed *not knowing when it's done*. But it
exposed the next itch: while a task ran, I had **zero visibility**. A spinner,
then — eventually — one big blob of final text. For a thirty-second task that's
fine. For a real coding task that reads files, edits several, runs tests? Two
silent minutes feels broken, even when it's working perfectly.

## The Problem

I wanted to *watch the agent work* — see it move through its steps live, the way
you'd watch a colleague's screen. But the coding CLIs run as one-shot
subprocesses: you hand them a prompt, you wait, you get output. There's no
natural "progress" coming out of a single blocking call.

## The Thinking

The tempting-but-wrong version was to stream token-by-token output from inside
each CLI. That means wrestling every engine's streaming format, and it fights
the one-shot subprocess model the whole system is built on. High effort, fragile.

The insight that unlocked it: **the progress signal was already there, one level
up.** Gajala's agent loop isn't a single call — it's Haiku deciding, then
calling a tool, then deciding again, then calling another. *Those tool calls are
the steps.* "Coding with Claude…", "Working on notes…", "Controlling the Mac…" —
each is a real, human-meaningful beat of progress. Streaming at the *agent-step*
granularity is the 80/20: it gives the live feeling without touching the CLIs at
all.

Two more decisions kept it clean:

- **NDJSON over a raw stream, not token soup.** The server emits one JSON object
  per line — `{step}` frames, then a terminal `{final}`. Trivial for the app to
  parse.
- **Don't break the old path.** The agent loop got an *optional* event callback.
  When nobody's listening (the Telegram bot, plain `/run`), it behaves exactly as
  before. Streaming is purely additive.

And it composed beautifully with story 03: while you're *in* the chat you watch
the steps live; the moment you *leave*, the completion push takes over. Same
event, two surfaces.

## What We Built

A new `/run/stream` endpoint that streams NDJSON progress as the agent works. The
app renders a live "status" bubble — a spinner with the step labels stacking up
as they happen — that gets swapped for the real reply when the final frame
lands. The completion push still fires as the safety net if you wandered off
mid-run.

## The Payoff

The wait stopped feeling like a void. Instead of *is it stuck?*, you see
"reading files… editing 3 files… running tests…" scroll by — the app feels
alive, and a two-minute task feels like watching work happen instead of staring
at a frozen screen. This is the difference between a toy and something you'd
actually trust with a real task.
