# 01 — Brain Dump

## The Spark

I kept using the notes feature for something the word "notes" didn't quite
capture. I wasn't filing tidy documents. I was *dumping* — half-formed ideas,
"don't forget," a bug I noticed in passing, a thing to tell someone. Fast, ugly,
from the phone, mid-thought.

## The Problem

"Notes" is a filing-cabinet word. It implies structure, titles, organization —
a small tax every time you use it. That tax is exactly what kills capture. The
whole value of jotting something down is that it's *frictionless*: the thought
leaves your head before it evaporates, and you sort it out later (or never).

The feature was already fine technically. The problem was that its *name* was
setting the wrong expectation for how I actually used it.

## The Thinking

This looks trivial — it's a rename — but it's a real product lesson: **naming
sets behavior.** Call it "Notes" and part of your brain wants to make each one
neat. Call it "Brain Dump" and you give yourself permission to be messy, which
means you actually capture the thought instead of losing it.

We considered leaving it alone (it worked!) and considered heavier ideas — tags,
categories, smart sorting. Rejected those. The point wasn't more structure; it
was *less friction*. The lightest possible change that shifted the mental model
won.

Underneath, it also fit the architecture cleanly: capture is
directory-independent. A brain dump doesn't belong to a project folder — it
lives in the central store and is reachable from anywhere. (That principle came
back later, in the session-sync design: some things just aren't about a
directory.)

## What We Built

The notes skill and its screen became **Brain Dump** — same reliable storage,
new framing. "New brain dump" instead of "New note." A dashboard tile that says
what it's for. The dump lands centrally, so it's there whether you're deep in a
project or just walking around with a thought.

## The Payoff

I use it more. That's the whole win. A capture tool is only as good as how
little it makes you think before capturing, and the rename lowered that bar to
the floor. The thoughts that used to evaporate now land somewhere.
