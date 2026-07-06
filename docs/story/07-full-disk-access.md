# 07 — The Full Disk Access Mystery

> A war story. Not a feature — a bug that kept coming back, and the satisfying
> moment it finally made sense.

## The Spark

The terminal kept losing Full Disk Access. I'd grant it in System Settings,
things would work, and then — days later — permission-denied errors again. I'd
remove the grant, re-add it, restart the terminal. Fixed. Until it wasn't. This
happened *repeatedly*, and it was maddening precisely because re-granting always
seemed to work, so it never felt like a real bug — just gremlins.

## The Problem

Background tools and subprocesses kept hitting `EPERM: operation not permitted`
reading my own project files. The Firebase CLI died from it (story 03). Random
skills would fail. And the "fix" — re-granting Full Disk Access — was temporary
every single time. Something was actively *undoing* my permissions.

## The Thinking

The breakthrough was refusing to accept "just re-grant it" as an answer and
asking *why it kept happening.* Two facts about where the project lived turned
out to be the whole story:

- My projects were on the **Desktop**. And `~/Desktop` isn't an ordinary folder
  on modern macOS — it's **TCC-protected**. Apps need explicit permission to
  read it, and that permission is exactly what kept getting reset.
- The Desktop was also **iCloud-synced** ("Desktop & Documents Folders" was on).
  So the iCloud file provider was constantly evicting and re-materializing files
  underneath me — which is a great way to make a background process trip over a
  file that isn't fully "there" at that instant.

Stack those together and add macOS point-updates quietly resetting the terminal's
grant, and the "gremlins" resolve into a clear mechanism. It was never flaky
permissions. It was **the wrong location** — a folder that was simultaneously
protected, synced, and periodically reset.

The lesson: when a fix keeps working and then failing, you're not fixing the
cause — you're resetting a symptom. The real question is *what keeps changing it
back.*

## What We Did

Moved the entire projects folder off the Desktop to `~/Projects` — the home root,
which is neither TCC-protected nor iCloud-managed. Then re-pointed everything
that knew the old path: the launchd services, the app's workspace config, the
`codaur` global link, the project-scanning defaults, and the `.env`. Verified the
server, bot, data stores, and usage reader all came up healthy from the new home.

## The Payoff

The bug didn't get *patched* — it got *deleted*. There's nothing to re-grant
anymore, because `~/Projects` was never protected or synced in the first place.
A recurring, morale-sapping mystery became a one-time move and a permanently
quiet system. Sometimes the best fix isn't code at all — it's putting things
where they should have been.
