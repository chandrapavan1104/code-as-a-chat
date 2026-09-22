"""Keep the existing phone updater's APK URL available across Mac restarts."""
import os
import plistlib
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server import config  # noqa: E402


def main():
    url = urlparse(config.APK_URL)
    if url.scheme != 'http' or not url.hostname or not url.port:
        raise SystemExit('APK_URL must be the existing http host:port download URL')
    if url.path != '/' + config.APK_DEST.name or not config.APK_DEST.is_file():
        raise SystemExit('APK_URL filename must match an existing APK_DEST')
    label = 'com.codeasachat.apk-share'
    logs = Path.home() / 'Library/Logs/code-as-a-chat'
    logs.mkdir(parents=True, exist_ok=True)
    plist = Path.home() / 'Library/LaunchAgents' / f'{label}.plist'
    # Bind only to the configured tailnet address, not every network interface.
    data = {
        'Label': label,
        'ProgramArguments': [sys.executable, '-m', 'http.server', str(url.port),
                             '--bind', url.hostname, '--directory', str(config.APK_DEST.parent)],
        'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 10,
        'StandardOutPath': str(logs / 'apk-share.log'),
        'StandardErrorPath': str(logs / 'apk-share.err'),
    }
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_bytes(plistlib.dumps(data))
    domain = f'gui/{os.getuid()}'
    subprocess.run(['launchctl', 'bootout', f'{domain}/{label}'], capture_output=True)
    subprocess.run(['launchctl', 'bootstrap', domain, str(plist)], check=True)
    print(f'Installed persistent APK share at {config.APK_URL}')


if __name__ == '__main__':
    main()
