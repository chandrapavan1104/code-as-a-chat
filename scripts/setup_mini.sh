#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# setup_mini.sh — provision a fresh Mac Mini to run Code-as-a-Chat.
#
# Run this AFTER you've copied over:
#   ~/Desktop/Projects/   (this repo + Codaur + your other projects)
#   ~/.codeasachat/        (notes/diary/memory/reminders + api_token)
#   ~/.claude/             (chat history so you can resume our conversation)
#
# It installs tooling, Python deps, the AI CLIs, codaur, the webcam helper,
# and registers the launchd services. It does NOT log you into anything and
# does NOT start the services — see MIGRATION.md for the auth + cutover steps.
# Safe to re-run.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
say() { printf "\n\033[1;36m▸ %s\033[0m\n" "$*"; }

# ── 1. Homebrew ──────────────────────────────────────────────────────────────
if ! command -v brew >/dev/null 2>&1; then
  say "Installing Homebrew (will ask for your password)…"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  eval "$(/opt/homebrew/bin/brew shellenv)"
fi

# ── 2. System tools the skills need ──────────────────────────────────────────
say "Installing CLI tools via brew…"
brew install node blueutil imagesnap ffmpeg gh wget || true
# Tailscale (the always-on tunnel) — GUI app, sign in afterwards
brew install --cask tailscale || true

# ── 3. Python 3.12 (our code needs 3.10+ — Xcode's 3.9 won't run it) ─────────
say "Installing Python 3.12 via brew…"
brew install python@3.12 || true
PY="$(brew --prefix)/bin/python3.12"
[ -x "$PY" ] || PY="$(command -v python3.12 || command -v python3)"
say "Using Python: $PY ($("$PY" --version 2>&1))"
"$PY" -m pip install --upgrade pip >/dev/null 2>&1 || true
"$PY" -m pip install -r "$REPO/requirements.txt"
# extras not always pinned in requirements
"$PY" -m pip install "python-telegram-bot>=21" "httpx>=0.27"
# launchd services must use THIS python (3.12), not Xcode's 3.9
export CODECHAT_PYTHON="$PY"

# ── 4. The three AI CLIs (subscription login done later) ─────────────────────
say "Installing Claude / Codex / Gemini CLIs…"
npm install -g @anthropic-ai/claude-code @openai/codex @google/gemini-cli || true

# ── 5. codaur (usage reporter) — linked from its repo in Projects ────────────
CODAUR_DIR="$HOME/Desktop/Projects/Codaur"
if [ -d "$CODAUR_DIR" ]; then
  say "Linking codaur…"
  ( cd "$CODAUR_DIR" && npm link )
else
  echo "   ⚠ Codaur not found at $CODAUR_DIR — /usage will be limited until linked"
fi

# ── 6. Webcam helper (compiled per-machine) ──────────────────────────────────
if command -v swiftc >/dev/null 2>&1; then
  say "Building WebcamSnap.app…"
  bash "$REPO/mac_helpers/build_webcamsnap.sh" || echo "   ⚠ build failed — run xcode-select --install then retry"
else
  echo "   ⚠ swiftc not found — run: xcode-select --install   (then re-run this script)"
fi

# ── 7. launchd services (plists regenerated with THIS Mac's python path) ─────
say "Registering launchd services (not starting yet)…"
bash "$REPO/scripts/codechat" install || true

cat <<'DONE'

────────────────────────────────────────────────────────────────────────────
 Tooling installed. NOT started yet — finish these by hand (see MIGRATION.md):

   1. AUTH:   claude login   ·   codex login   ·   gemini   ·   firebase login
   2. TAILSCALE: open -a Tailscale  → sign in  →  tailscale serve --bg 8000
   3. PERMISSIONS (System Settings → Privacy & Security):
        • Screen Recording  → the python that runs the server (for /mac screenshot)
        • Camera            → WebcamSnap.app  (for /mac photo)
        • Bluetooth         → terminal/python (for /mac bluetooth)
   4. CUTOVER: make sure the OLD Mac's bot is stopped, then:
        ./scripts/codechat start
   5. RESUME OUR CHAT:
        cd ~/Desktop/Projects/Code-as-a-chat && claude -c
────────────────────────────────────────────────────────────────────────────
DONE
