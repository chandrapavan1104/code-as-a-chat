# 02 — Codaur

## The Spark

I work across three AI coding CLIs — Claude, Codex, Gemini — sometimes all in a
day. And I had no honest picture of how much I was burning through each. Which
one was near its limit? How many threads had I run today? I was flying blind on
my own usage.

## The Problem

Every engine keeps its usage locally, in its own shape, in its own hidden
folder. Claude has rate-limit windows in one format. Codex logs token counts in
JSONL rollouts. Gemini tracks something else again. There was no single place
that said, plainly: *here's where you stand across all of them.* And I wanted
that on the phone, as a screen I could glance at (and, honestly, flex a little).

## The Thinking

First instinct was to bolt usage-reading straight into the server. Rejected it —
usage parsing is fiddly and engine-specific, and it deserved to be its own
thing I could run from the terminal too. So it became a separate tool of mine:
**Codaur**, a small CLI that reads each engine's local usage and emits one clean
JSON shape the app can render.

The build was a lesson in *reading other people's undocumented storage*:

- The data lives in per-engine stores that change without warning. At one point
  Claude moved its captured rate limits out of one field
  (`latestRateLimitSnapshot`) into a new array (`limitUsage[]`) — so the reader
  had to understand the new schema and still fall back to the old one.
- I tried wiring in a fourth engine (Antigravity), but it stores everything as
  opaque protobuf blobs with no usable token data — so I cut it and used Gemini
  instead. Knowing when to *drop* a source is part of the craft.
- Engines don't even agree on what a "limit" is. Claude and Codex expose
  token windows (5-hour, weekly); Gemini exposes a daily *request* count. So the
  app couldn't assume a fixed layout — it had to render *whatever windows an
  engine reports*, with generic labeled bars.

Two smaller touches made it feel finished: **plan chips** (a "Pro" tag for
Claude and Gemini, "Plus" for Codex) pulled from config, and a rename of the
whole screen from the generic "Usage" to **Codaur** — giving my own tool its own
identity in the app.

## What We Built

`codaur` reads Claude/Codex/Gemini usage locally and returns a normalized report:
totals (threads, tokens, today's tokens), plus a per-window list of limit bars.
The server's `/api/usage` endpoint shells out to it, filters to the engines I
actually use, attaches plan labels, and the app draws a card per engine — token
counts where they exist, request counts where they don't, a colored bar per
window.

## The Payoff

One glance, three engines, real numbers. I stopped guessing about limits, and I
got a genuinely satisfying screen to show people: "yeah, that's my AI fuel
gauge — for all three, on my phone." A blind spot became a dashboard.
