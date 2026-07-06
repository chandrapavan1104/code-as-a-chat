# 06 — Session Sync

> This is the chapter being written *right now*. The design below is settled;
> the build follows. It's the deepest of the lot, so we thought it through
> carefully before touching code.

## The Spark

The features so far made a *single* request from the phone feel great. But real
coding isn't one request — it's a *thread*. You ask, it does, you ask again
building on the last thing. And I don't just work from the phone; I switch to the
Mac, I switch between Claude and Codex. I wanted all of that to feel like **one
continuous train of thought**, not a pile of disconnected one-shots.

## The Problem

Three messes, really:

1. **No continuity.** Every request spun up a *brand-new* Claude session and
   threw the ID away. Turn 2 didn't remember turn 1's session. And `~/.claude`
   was filling with orphan single-message sessions — clutter with no memory.
2. **Model-switching.** I bounce between Claude, Codex, Gemini. I wanted them to
   stay in sync with the *progress* in a directory even as I switched.
3. **Phone ↔ Mac.** I wanted to start something on the phone and pick it up on
   the Mac, and back — without losing the thread.

## The Thinking

This is where a wrong assumption almost sent us down a bad path — and catching
it *was* the design.

**The tempting wrong idea:** "make one conversation span all the models." It
can't. A Claude session and a Codex session are separate things in separate
stores; you can't resume one inside the other. Chasing a shared transcript
across models is a dead end.

**The correction that unlocked everything:** the thing worth syncing across
models isn't the *chat* — it's the *progress*. And progress already lives in two
shared places: the **files on disk** (Claude edits it, Codex sees it — free), and
the shared **CLAUDE.md / AGENTS.md / GEMINI.md** notebook, which this project
already auto-syncs. So:

> Each model has its own memory of the *conversation*. All models share the same
> memory of the *project* — the files, plus the notebook.

**The second insight:** phone and Mac aren't two devices to sync. The phone runs
Claude *on the Mac*. There's one `~/.claude`. So a session started from the phone
literally *is* a Mac session — `claude --resume <id>` in the terminal drops you
into the exact same thread. The sync is basically free; we were just throwing the
IDs away.

**The routing question:** could the Haiku orchestrator be smart enough to tag
each request to the right session? Partly. Coarse decisions ("is this a project
or a general question?") are reliable. Fine-grained "which of six threads does
this belong to?" is fallible and *nags*. So we anchor on the **directory** — a
hard, deterministic signal — instead of betting on the model guessing. One active
session per **(directory, model)**. The folder decides; no guessing needed.

**Where do non-project things go?** Not every ask belongs to a directory. Notes
and reminders live in a central store — no folder at all. General questions get a
dedicated **`general`** home-base directory, so they never pollute a real
project's session. Three buckets, one clear rule each.

We also shaped the *feel*, and drew one line deliberately: the chat should show
which `directory · model` you're in (that pair *is* the session), let you switch
both from the chat, and — narrowly — have Gajala double-check you *only* when a
request names a **different known project** than the one you're in. The broad
"does this vibe belong here?" check was rejected on purpose: it would cry wolf.

## The Plan

- A **`general`** home base as the default when you're not in a project.
- One **persistent session per (directory, model)**, reused via resume — killing
  the clutter and giving real continuity.
- The shared notebook as the **cross-model** progress layer.
- A chat header showing `directory · model`, a switcher for both, and a
  **narrow** wrong-directory confirmation.
- "Continue on Mac: `claude --resume <id>`" — one command to hand a thread off.

## The Payoff (the goal)

One coherent brain. A thought becomes a thread; the thread remembers itself;
switching models keeps the *progress*; and any thread can move between phone and
Mac with a single command. This is the piece that turns "I can run things from my
phone" into "I can genuinely *work* from my phone" — which was the whole point,
all along.
