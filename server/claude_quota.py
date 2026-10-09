"""Refresh subscription quotas through Claude's native /usage, without a model call.

Headless -p runs never invoke statusLine. A bounded interactive refresh lets the
Codaur capture receive Claude's own displayed data without reading login secrets.
"""
import errno
import fcntl
import json
import os
from pathlib import Path
import pty
import re
import select
import shutil
import signal
import struct
import subprocess
import termios
import threading
import time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import pyte

FRESH_SECONDS = 300
_LOCK = threading.Lock()
_last_attempt = 0.0
_last_error = None


def capture_path():
    return Path(os.getenv('XDG_CACHE_HOME', str(Path.home()/'.cache'))) / 'codaur/claude/rate-limits.json'


def snapshot():
    try:
        data = json.loads(capture_path().read_text())
        captured = datetime.fromisoformat(data['capturedAt'].replace('Z', '+00:00')).timestamp()
        age = time.time() - captured
        return data, captured, 0 <= age <= FRESH_SECONDS
    except (OSError, ValueError, KeyError, TypeError):
        return {}, None, False


def _native_refresh(cwd, timeout=20):
    binary = shutil.which('claude')
    if not binary:
        return 'Claude CLI is not installed'
    # This path must already be trusted: never answer a trust/login/permission prompt.
    before = snapshot()[1]
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 50, 120, 0, 0))
    proc = None
    try:
        def terminal_session():
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
        env = {**os.environ, 'TERM': 'xterm-256color', 'COLUMNS': '120', 'LINES': '50'}
        proc = subprocess.Popen([binary, '--model', 'haiku'], cwd=cwd, env=env,
            stdin=slave, stdout=slave, stderr=slave, preexec_fn=terminal_session)
        os.close(slave)
        slave = -1
        deadline = time.monotonic() + timeout
        output = ''
        screen = pyte.Screen(120, 50)
        stream = pyte.Stream(screen)
        last_update = time.monotonic()
        sent = False
        while time.monotonic() < deadline and proc.poll() is None:
            if snapshot()[1] != before and snapshot()[2]:
                return None
            if select.select([master], [], [], .1)[0]:
                try:
                    chunk = os.read(master, 65536).decode(errors='replace')
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        break
                    raise
                stream.feed(chunk)
                output = '\n'.join(screen.display)
                last_update = time.monotonic()
                lowered = output.lower()
                if any(x in lowered for x in ('trust this folder', 'choose the login method',
                                             'please run /login', 'sign in to continue')):
                    return 'Claude needs workspace trust or sign-in on the Mac'
                if not sent and '❯' in output and 'Claude Code' in output:
                    os.write(master, b'/usage\r')
                    sent = True

            if sent and time.monotonic() - last_update > 1 and 'Refreshing' not in output:
                quotas = parse_usage_screen(output)
                if quotas:
                    save_capture(quotas)
                    return None
        return 'Claude did not supply a fresh quota snapshot'
    except (OSError, subprocess.SubprocessError):
        return 'Claude quota refresh could not start'
    finally:
        if proc is not None:
            try:
                proc.terminate()
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        os.close(master)
        if slave >= 0:
            os.close(slave)



def parse_usage_screen(text):
    """Only accept both subscription windows from the built-in usage dialog."""
    five = re.search(r'Current session\s+.*?(\d+(?:\.\d+)?)%\s*used', text, re.S)
    week = re.search(r'Current week\s*\(all models\).*?(\d+(?:\.\d+)?)%\s*used', text, re.S)
    if not five or not week:
        return None
    values = [float(five.group(1)), float(week.group(1))]
    if any(v < 0 or v > 100 for v in values):
        return None
    # Display percentages are the native rounded values. Capture age bounds
    # freshness even when a CLI version omits a machine-readable reset time.
    result = {name: {'used_percentage': value}
              for name, value in zip(('five_hour', 'seven_day'), values)}
    for name, block in (('five_hour', text[five.end():week.start()]),
                        ('seven_day', text[week.end():])):
        reset = _reset_time(block)
        if reset is not None:
            result[name]['resets_at'] = reset
    return result


def _reset_time(block):
    match = re.search(r'Resets\s+([^\n]+?)\s+\(([A-Za-z_]+/[A-Za-z_/]+)\)', block)
    if not match:
        return None
    try:
        zone = ZoneInfo(match.group(2))
        now = datetime.now(zone)
        value = match.group(1).strip()
        if value.startswith('tomorrow at '):
            day = now + timedelta(days=1)
            value = value[len('tomorrow at '):]
        else:
            day = now
        for fmt in ('%I:%M%p', '%I%p', '%b %d at %I:%M%p', '%b %d at %I%p'):
            try:
                parsed = datetime.strptime(value, fmt)
                if fmt.startswith('%b'):
                    reset = parsed.replace(year=now.year, tzinfo=zone)
                else:
                    reset = day.replace(hour=parsed.hour, minute=parsed.minute,
                                        second=0, microsecond=0)
                return reset.timestamp() if reset > now else None
            except ValueError:
                continue
    except (KeyError, ValueError):
        pass
    return None


def save_capture(quotas):
    path = capture_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {'capturedAt': datetime.now(timezone.utc).isoformat(),
               'source': 'Claude native /usage display', 'rate_limits': quotas}
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload))
    temporary.replace(path)


def trusted_workspace(cwd):
    """Resolve a deployment worktree to its already configured owner checkout."""
    root = Path(cwd)
    try:
        marker = (root / '.git').read_text().strip()
        if marker.startswith('gitdir: '):
            gitdir = (root / marker[8:]).resolve()
            common = (gitdir / (gitdir / 'commondir').read_text().strip()).resolve()
            if common.name == '.git':
                return str(common.parent)
    except OSError:
        pass
    return str(root)

def refresh(cwd):
    global _last_attempt, _last_error
    if snapshot()[2]:
        return None
    with _LOCK:
        if snapshot()[2]:
            return None
        if time.monotonic() - _last_attempt < FRESH_SECONDS:
            return _last_error or 'Waiting for the next quota refresh'
        _last_attempt = time.monotonic()
        _last_error = _native_refresh(trusted_workspace(cwd))
        return _last_error


def metadata():
    _, captured, fresh = snapshot()
    return {'quota_updated_at': captured, 'quota_stale': not fresh,
            'quota_source': 'Claude native /usage',
            'quota_error': None if fresh else (_last_error or 'Claude quota capture is stale or unavailable')}
