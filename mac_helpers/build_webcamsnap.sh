#!/usr/bin/env bash
# Build & sign WebcamSnap.app — the camera-capture helper used by /mac photo.
# Must run on each machine (the binary is compiled + macOS grants camera
# permission per the app's code signature). Idempotent.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$HOME/Applications/WebcamSnap.app"

echo "Building WebcamSnap.app …"
mkdir -p "$HOME/Applications"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp "$HERE/Info.plist" "$APP/Contents/Info.plist"

swiftc -O -o "$APP/Contents/MacOS/WebcamSnap" "$HERE/WebcamSnap.swift" \
  -framework AVFoundation -framework CoreImage -framework Foundation

codesign --force --deep --sign - "$APP"
echo "Built: $APP"
echo "NOTE: first /mac photo will prompt for camera access — open the app once"
echo "      (open $APP) and click Allow, or grant it in System Settings > Camera."
