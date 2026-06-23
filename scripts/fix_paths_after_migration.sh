#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# fix_paths_after_migration.sh — run ON THE MINI after copying, if the Mini's
# username differs from the old Mac's (e.g. old=chandrapavan1104, new=dark_mamba).
#
# The runtime data (notes/diary/memory DBs) is username-agnostic already.
# This only fixes the few spots that bake in an absolute /Users/<old> path:
#   • .env            WORKSPACE_DIR / PROJECTS_PARENT_DIR  → use ~ (portable)
#   • state.json      workspace_dir                        → new home
#   • ~/.claude/projects/-Users-<old>-…  dirs              → rename to -<new>-
#     (so `claude -c` and the project memory resolve under the new home)
#
# Usage:  bash scripts/fix_paths_after_migration.sh [old_username]
#         (old_username defaults to chandrapavan1104)
# Safe to re-run.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

OLD="${1:-chandrapavan1104}"
NEW="$(whoami)"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
say() { printf "\n\033[1;36m▸ %s\033[0m\n" "$*"; }

if [ "$OLD" = "$NEW" ]; then
  echo "Username unchanged ($NEW) — nothing to fix. 🎉"
  exit 0
fi
say "Remapping /Users/$OLD  →  /Users/$NEW (\$HOME=$HOME)"

# ── 1. .env: switch absolute project paths to ~ (works for any user) ─────────
ENV="$REPO/.env"
if [ -f "$ENV" ]; then
  say "Fixing .env paths…"
  # /Users/<old>/Desktop/...  → ~/Desktop/...
  sed -i '' -E "s#/Users/$OLD/#~/#g" "$ENV"
  grep -E "WORKSPACE_DIR|PROJECTS_PARENT_DIR" "$ENV" || true
fi

# ── 2. state.json: active workspace path → new home ──────────────────────────
STATE="$HOME/.codeasachat/state.json"
if [ -f "$STATE" ]; then
  say "Fixing state.json…"
  sed -i '' -E "s#/Users/$OLD/#$HOME/#g" "$STATE"
  cat "$STATE"
fi

# ── 3. Claude project/memory dirs: rename encoded paths to the new user ──────
CLAUDE_PROJECTS="$HOME/.claude/projects"
if [ -d "$CLAUDE_PROJECTS" ]; then
  say "Renaming Claude session dirs (-Users-$OLD-… → -Users-$NEW-…)…"
  shopt -s nullglob
  for d in "$CLAUDE_PROJECTS"/-Users-"$OLD"-*; do
    base="$(basename "$d")"
    newbase="${base/-Users-$OLD-/-Users-$NEW-}"
    target="$CLAUDE_PROJECTS/$newbase"
    if [ -e "$target" ]; then
      echo "  skip (exists): $newbase"
    else
      mv "$d" "$target"
      echo "  $base → $newbase"
    fi
  done
fi

cat <<DONE

────────────────────────────────────────────────────────────────────────────
 Paths remapped for user '$NEW'.
   • Resume our chat:   cd ~/Desktop/Projects/Code-as-a-chat && claude -c
   • If /projects looks off:  /projects switch code-as-a-chat   (rebuilds state)
   • Restart services:  ./scripts/codechat restart
────────────────────────────────────────────────────────────────────────────
DONE
