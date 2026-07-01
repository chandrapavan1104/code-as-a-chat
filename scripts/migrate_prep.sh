#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# migrate_prep.sh — run on the OLD Mac (MacBook Air) before migrating.
#
# Telegram allows only ONE poller per bot token, so this STOPS the bot here
# (so it can run on the Mini instead) and prints the exact copy commands.
# It does not delete anything.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
say() { printf "\n\033[1;36m▸ %s\033[0m\n" "$*"; }

say "Stopping local services (frees the Telegram token for the Mini)…"
bash "$REPO/scripts/codechat" stop || true

say "Sizes to copy:"
du -sh "$HOME/.codeasachat" "$HOME/.claude" "$HOME/Desktop/Projects" 2>/dev/null || true

cat <<EOF

────────────────────────────────────────────────────────────────────────────
 COPY THESE THREE THINGS TO THE MINI (same username: $(whoami))

 Option A — rsync over SSH (enable Remote Login on the Mini first:
            System Settings → General → Sharing → Remote Login):

   MINI=chandrapavan1104@<mini-name>.local      # or the Mini's LAN IP

   rsync -avh --progress  ~/.codeasachat/   \$MINI:~/.codeasachat/
   rsync -avh --progress  ~/.claude/        \$MINI:~/.claude/
   rsync -avh --progress  ~/Desktop/Projects/  \$MINI:~/Desktop/Projects/

   # Faster (skip node_modules, reinstall per-project on the Mini):
   # rsync -avh --progress --exclude node_modules --exclude .next --exclude dist \\
   #   ~/Desktop/Projects/  \$MINI:~/Desktop/Projects/

 Option B — external SSD:
   cp -R ~/.codeasachat ~/.claude ~/Desktop/Projects  /Volumes/<DRIVE>/caac_migrate/
   # then on the Mini, copy each back into \$HOME

 NOTE: .env and the api_token travel inside these folders (rsync copies them).
       Do NOT git-clone the repo instead — you'd lose .env.
────────────────────────────────────────────────────────────────────────────

 After copying, on the MINI run:
   cd ~/Desktop/Projects/Code-as-a-chat && bash scripts/setup_mini.sh
 Then follow MIGRATION.md.

 Leave this Air bot STOPPED. To fully retire it later:
   ./scripts/codechat uninstall
EOF
