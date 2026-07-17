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
from server import config
from server.skills.base import Skill
from server.skills import register


async def build_and_deploy(timeout: int = 600) -> tuple[bool, str]:
    """Sync repo app sources → build → deploy. Returns (ok, message)."""
    repo = str(config.REPO_DIR)
    dev = str(config.FLUTTER_APP_DIR)
    script = f"""
set -e
export PATH="{config.FLUTTER_BIN_DIR}:$PATH"
export JAVA_HOME="$(/usr/libexec/java_home -v 17)"
rsync -a "{repo}/clients/gajala/lib/" "{dev}/lib/"
rsync -a "{repo}/clients/gajala/android/app/src/main/" "{dev}/android/app/src/main/"
cp "{repo}/clients/gajala/pubspec.yaml" "{dev}/pubspec.yaml"
cd "{dev}"
flutter pub get >/dev/null
flutter build apk --release
cp "{dev}/build/app/outputs/flutter-apk/app-release.apk" "{config.APK_DEST}"
"""
    proc = await asyncio.create_subprocess_exec(
        "bash", "-lc", script,
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
    return True, f"Built + deployed ✅\nInstall/update: {config.APK_URL}"


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
