# Migrating Code-as-a-Chat → Mac Mini

Move the whole thing from the MacBook Air to the always-on Mac Mini, **keeping
all data and resuming our Claude Code chat**. Selective copy of three things
onto a cleanly-tooled Mini.

> **Username note:** simplest is for the Mini's user to be **`chandrapavan1104`**
> (same as the Air) — then everything Just Works. If the Mini uses a different
> username (e.g. `dark_mamba`), that's fine too: the data is username-agnostic,
> and after copying you run **`bash scripts/fix_paths_after_migration.sh`** on
> the Mini to remap the few absolute paths (.env, state.json) and rename the
> Claude session dirs so `claude -c` resumes this chat.

---

## What moves (and why)

| Source | Size | Why |
|---|---|---|
| `~/Desktop/Projects/` | ~9.4 G | all repos: this project, **Codaur**, + your ~25 dev projects. Also carries `.env` and the Expo app. |
| `~/.codeasachat/` | ~0.6 M | **the irreplaceable data** — notes.db, diary.db, conversations.db (Gajala memory), reminders.db, state.json, **api_token** |
| `~/.claude/` | ~31 M | chat history + the project memory → lets you **resume this exact conversation** |

What does **not** copy and must be redone on the Mini (steps below):
re-login the CLIs, Tailscale, and **all camera/screen/bluetooth permissions**
(TCC is per-machine — it never transfers).

---

## Phase 1 — On the Air (prep)

```bash
cd ~/Desktop/Projects/Code-as-a-chat
./scripts/migrate_prep.sh
```

This **stops the Air's bot** (Telegram allows one poller per token) and prints
the exact copy commands. Leave the bot stopped.

## Phase 2 — Copy the three folders

Easiest is rsync over SSH (enable **Remote Login** on the Mini:
System Settings → General → Sharing → Remote Login), same as `migrate_prep.sh`
prints:

```bash
MINI=chandrapavan1104@<mini-name>.local
rsync -avh --progress  ~/.codeasachat/      $MINI:~/.codeasachat/
rsync -avh --progress  ~/.claude/           $MINI:~/.claude/
rsync -avh --progress  ~/Desktop/Projects/  $MINI:~/Desktop/Projects/
```

(Or an external SSD — see `migrate_prep.sh` Option B.)

## Phase 3 — On the Mini (tooling)

Open Terminal on the Mini and:

```bash
xcode-select --install                 # for swiftc (webcam helper) + git
cd ~/Desktop/Projects/Code-as-a-chat
bash scripts/setup_mini.sh
```

Installs Homebrew, node, the system tools (blueutil/imagesnap/ffmpeg/gh),
Python deps, the three AI CLIs, links **codaur**, builds **WebcamSnap.app**,
and registers the launchd services (without starting them).

> Python: the services use whatever `python3` resolves to. If it's < 3.11,
> install Python 3.11+ (python.org or `brew install python@3.12`) and re-run.

## Phase 4 — Re-login (these don't transfer)

```bash
claude login        # your Claude Pro
codex login         # ChatGPT Plus
gemini              # Google account (interactive)
firebase login      # Google account
```

(`~/.claude` chat history copied over already; only the *auth* may need redo.)

## Phase 5 — Permissions (TCC — never transfers)

System Settings → Privacy & Security:
- **Screen Recording** → enable for the `python3` that runs the server (needed
  for `/mac screenshot`). It'll prompt on first use, or add it manually.
- **Camera** → open `~/Applications/WebcamSnap.app` once, click Allow (for `/mac photo`).
- **Bluetooth** → enable for Terminal/python (for `/mac bluetooth`).

## Phase 6 — Tailscale + cutover

```bash
open -a Tailscale                    # sign in (same account as your phone)
TS=/Applications/Tailscale.app/Contents/MacOS/Tailscale
$TS status                           # note the Mini's  <name>.ts.net
$TS serve --bg 8000                  # expose the API over the tailnet (HTTPS)

# Make sure the AIR bot is stopped (Phase 1), then start the Mini:
cd ~/Desktop/Projects/Code-as-a-chat
./scripts/codechat start
./scripts/codechat status            # both server + bot running?
```

- **Telegram** keeps working with the same token (now served from the Mini).
- **Expo app**: the api_token is unchanged (copied in `~/.codeasachat`), but the
  server URL changes to the Mini's `https://<name>.ts.net` — update it in the
  app's Setup screen.

## Phase 7 — Resume our chat

```bash
cd ~/Desktop/Projects/Code-as-a-chat
claude -c                            # continue most recent conversation here
# or explicitly:
# claude --resume fb8d4d71-7587-4aa5-b7cf-446945b305c6
```

A fresh session also works — the project memory + `CLAUDE.md`/`AGENTS.md` give
full context. Tell it: *"continue the Code-as-a-Chat migration, read MIGRATION.md"*.

## Phase 8 — Retire the Air

Once the Mini is verified working:

```bash
# on the AIR:
./scripts/codechat uninstall         # remove its launchd services for good
```

---

## Verification checklist (on the Mini)

- [ ] `./scripts/codechat status` → server + bot running
- [ ] `curl localhost:8000/health` → `{"status":"ok"}`
- [ ] Telegram: message Gajala → it replies
- [ ] `/status` → live CPU/RAM
- [ ] `/notes` → your notes are all there
- [ ] `/diary recent` → Anna remembers
- [ ] `/mac photo` → webcam photo arrives (after Camera grant)
- [ ] Expo app (new `.ts.net` URL) → connects, chats
- [ ] `claude -c` → this conversation resumes

## Gotchas recap

1. **Same username** (`chandrapavan1104`) or chat-resume + memory paths break.
2. **One bot per token** — Air bot must be stopped before the Mini's starts.
3. **TCC permissions** (camera/screen/bluetooth) are per-machine — re-grant.
4. **Tailscale** = new device, new `.ts.net` name → update the Expo app URL.
5. **`.env` travels with the folder copy** — don't git-clone instead of rsync.
