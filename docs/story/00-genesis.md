# 00 — Genesis

## The Spark

I had a Mac that never slept. Always on, always plugged in, sitting there with
all my projects, all my AI CLIs already logged in — Claude, Codex, Gemini. And
I had a phone in my pocket that could do almost nothing with any of it.

The frustration was specific: an idea would hit me away from my desk — a fix, a
refactor, a "wait, did that deploy go through?" — and the answer was always
"note it down and do it when you're back." The most powerful dev environment I
owned was fully idle whenever I wasn't physically in front of it.

## The Problem

I didn't want a remote desktop. Tiny SSH-in-a-terminal on a phone is misery,
and screen-sharing a Mac to a 6-inch screen is worse. I wanted to *talk* to my
machine. Natural language in, real work out. "Run the tests on Kirana." "What's
eating my CPU?" "Add a note to look at the auth bug tomorrow."

The hard part isn't any one command — it's that a phone chat has to route an
open-ended sentence to the *right capability*, run it on a real machine, and
reply in something readable on a phone, not a wall of terminal output.

## The Thinking

Three decisions set the whole shape of the project:

- **Skills, not endpoints.** Instead of hard-coding features, every capability
  is a self-describing "skill" that drops into a folder and auto-registers.
  Running Claude/Codex/Gemini, notes, reminders, ports, sessions, Mac control —
  each is just a skill. Adding one needs zero wiring. This kept the system open
  to everything I'd think of later (and I thought of a lot).

- **An LLM as the router.** A fast, cheap model (Haiku) reads the message,
  picks the skill(s), chains them if needed, and reformats the result for a
  phone. This is the difference between "a bot with 40 commands to memorize"
  and "just say what you want."

- **Local-first, on purpose.** All my data — notes, diary, reminders,
  conversation memory — stays in `~/.codeasachat/` on my own machine. The only
  things that talk to the outside world are the AI CLIs I already use anyway.
  Nothing new leaves the house.

Then there was the personality question. A tool you talk to every day
shouldn't sound like a form. So the agent became **Gajala** — a nod to the
Telugu movie *Venky* — with a Telugu-bestie voice and a running gag that she's
"Gajala... from Washington DC." Seasoning, not the meal: the persona colors the
replies, never the actual routing.

## What We Built

A FastAPI orchestrator on the Mac exposing the skill set, with two front doors:
`/run` for a single reply and (later) `/run/stream` for live progress. Two
clients: a Telegram bot to start, and a native Flutter Android app — Gajala —
as the real home. Tailscale to reach the Mac privately from anywhere. launchd
to keep it alive. The phone became a proper remote for a full dev server.

## The Payoff

The idle machine woke up. The gap between "I have an idea" and "it's running"
collapsed from *when I get home* to *right now, from the chat*. Everything that
follows in these stories is one more thing that gap used to block.
