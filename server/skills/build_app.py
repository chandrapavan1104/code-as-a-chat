"""
build skill — rebuild the Gajala Android APK from the repo and deploy it to the
share, returning the install link.

Automates the manual build dance: the repo's tracked app sources are rsynced into
the buildable Flutter copy (FLUTTER_APP_DIR, which holds the gitignored
google-services.json / signing config), then `flutter build apk --release` runs
and the artifact is copied to APK_DEST (served over Tailscale at APK_URL).

Exposes build_and_deploy() so the fix agent can rebuild after an app-side change.
"""

import asyncio
import json
import re
import time
from pathlib import Path

from server import config
from server.skills.base import Skill
from server.skills import register


def _version_name(source_repo: Path | str | None = None) -> str:
    """versionName from the repo pubspec (the `version: X.Y.Z+build` line)."""
    try:
        repo = Path(source_repo) if source_repo else Path(config.REPO_DIR)
        text = (repo / "clients" / "gajala" / "pubspec.yaml").read_text()
        m = re.search(r"^version:\s*([0-9.]+)", text, re.MULTILINE)
        return m.group(1) if m else "0.0.0"
    except Exception:
        return "0.0.0"


async def build_and_deploy(timeout: int = 600,
                           source_repo: Path | str | None = None) -> tuple[bool, str]:
    """Sync repo app sources → build → deploy. Returns (ok, message).

    Each build stamps a fresh versionCode via `--build-number=<epoch>` (no pubspec
    edit, so it never dirties the tree the fix agent guards) and writes
    apk_version.json next to the APK so the app can detect "a newer build exists"."""
    repo = str(Path(source_repo) if source_repo else Path(config.REPO_DIR))
    dev = str(config.FLUTTER_APP_DIR)
    build_number = int(time.time())          # monotonically increasing versionCode
    version_name = _version_name(source_repo)
    script = f"""
set -e
export PATH="{config.FLUTTER_BIN_DIR}:$PATH"
export JAVA_HOME="$(/usr/libexec/java_home -v 17)"
rsync -a "{repo}/clients/gajala/lib/" "{dev}/lib/"
rsync -a "{repo}/clients/gajala/android/app/src/main/" "{dev}/android/app/src/main/"
cp "{repo}/clients/gajala/pubspec.yaml" "{dev}/pubspec.yaml"
cd "{dev}"
flutter pub get >/dev/null
flutter build apk --release --build-number={build_number} --build-name={version_name}
cp "{dev}/build/app/outputs/flutter-apk/app-release.apk" "{config.APK_DEST}"
"""
    proc = await asyncio.create_subprocess_exec(
        "bash", "-lc", script,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return False, f"build timed out after {timeout}s"

    out = out_b.decode(errors="replace")
    if proc.returncode != 0:
        # Surface the tail — the useful part of a gradle/flutter failure.
        tail = "\n".join(out.strip().splitlines()[-15:])
        return False, f"build failed:\n{tail}"

    # Publish the version manifest so the app's updater can compare.
    try:
        meta = {"build_number": build_number, "version_name": version_name,
                "built_at": int(build_number), "url": config.APK_URL}
        Path(config.APK_DEST).with_name("apk_version.json").write_text(json.dumps(meta))
    except Exception:
        pass
    return True, f"Built + deployed ✅ (build {build_number})\nInstall/update: {config.APK_URL}"


class BuildAppSkill(Skill):
    name = "build"
    description = "Rebuild the Gajala APK and deploy it, returning the install link."
    final_output = True
    agent_doc = ('Rebuild + deploy the Gajala Android app after a code change. '
                 'No args. Returns the APK install link. Slow (~1 min).')

    async def run(self, prompt: str = "", **kwargs) -> str:
        ok, msg = await build_and_deploy(timeout=config.FIX_TIMEOUT)
        return msg


register(BuildAppSkill())
