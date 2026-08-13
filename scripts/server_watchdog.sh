#!/usr/bin/env bash
# Independent launchd watchdog for the Gajala API.

set -u

UID_VALUE="$(id -u)"
LABEL="gui/${UID_VALUE}/com.codeasachat.server"
PLIST="$HOME/Library/LaunchAgents/com.codeasachat.server.plist"
LOG_DIR="$HOME/Library/Logs/code-as-a-chat"
LOG_FILE="$LOG_DIR/watchdog.log"

mkdir -p "$LOG_DIR"

if /usr/bin/curl -fsS --max-time 4 http://127.0.0.1:8000/health >/dev/null 2>&1; then
    exit 0
fi

[[ -f "$PLIST" ]] || exit 0

# The deployment guard rewrites this plist immediately before an intentional
# restart. Give it time to verify or roll back without racing a second restart.
NOW="$(date +%s)"
MODIFIED="$(stat -f %m "$PLIST" 2>/dev/null || echo "$NOW")"
if (( NOW - MODIFIED < 120 )); then
    exit 0
fi

if launchctl print "$LABEL" >/dev/null 2>&1; then
    echo "$(date -u +%FT%TZ) API unhealthy; asking launchd to restart it" >> "$LOG_FILE"
    launchctl kickstart -k "$LABEL" >> "$LOG_FILE" 2>&1 || true
else
    echo "$(date -u +%FT%TZ) API service missing; bootstrapping it" >> "$LOG_FILE"
    launchctl bootstrap "gui/${UID_VALUE}" "$PLIST" >> "$LOG_FILE" 2>&1 || true
fi
