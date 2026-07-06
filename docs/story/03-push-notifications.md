# 03 — Push Notifications

## The Spark

I'd ask Gajala to do something real — a task that takes a couple of minutes —
and then... I'd just have to *sit there*. If I locked my phone or switched apps,
I had no idea when it finished. The app could talk to me only while I was
staring at it. That's not how messaging works. That's not how anything on a
phone works.

## The Problem

Two layers, actually.

1. **Infrastructure:** the app had no way to receive a push at all. No Firebase
   project, no device registration, no server-side sender.
2. **The real feature:** once push worked, I wanted a *chat-style* completion
   ping — "your reply is ready" — but only when I've **left the chat**. If I'm
   sitting in the conversation watching it, a notification is noise. Exactly the
   WhatsApp rule: you only get buzzed for a message when you're *not* looking at
   that thread.

## The Thinking

**Getting FCM working was its own small saga.** The clean path was supposed to
be the Firebase CLI — but it kept dying with permission errors (a preview of the
Full Disk Access mystery in story 07). So I pivoted to the Firebase *web
console*: create the project by hand, download `google-services.json` and a
service-account key. Even pasting the key was a fight — a terminal heredoc
truncated it — until I just opened it in TextEdit and pasted there. Small stuff,
but it's the real texture of shipping.

**The completion-notification design had a subtle core:** who decides whether to
show the ping? The server can't know if I'm looking at the chat. So the rule
became: the **server always sends** a `chat_reply` push on completion; the
**app suppresses** it if it's foregrounded on that exact conversation. That's
the WhatsApp behavior, and it's robust — because it also survives the app being
killed.

That last point drove the biggest call: **why push at all, instead of just a
local notification when the reply arrives?** Because on a long task, Android can
suspend the app and kill the network socket. If the app is the only thing that
knows the reply came back, a suspended app means *no notification ever*. But the
**server** always finishes the work — so if the *server* fires the push, the
ping arrives no matter what happened to the app. Robustness won over simplicity.

## What We Built

A full FCM pipeline: a server-side sender (with dead-token pruning), device
registration from the app, and a `notify` flag on `/run`. When set, the server
fires a completion push tagged with the session, carrying a one-line preview of
the reply. The app shows it as a real notification *unless* you're already in
that chat, and tapping it deep-links straight back into the conversation
(foreground, background, or cold-launch from killed).

## The Payoff

Gajala started behaving like a person texting me back. Fire off a task, pocket
the phone, get a buzz when it's done, tap to read it. The "sit and wait" tax
disappeared — which is the whole point if you want to actually *work* from a
phone instead of babysitting it.
