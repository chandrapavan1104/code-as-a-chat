"""Discover selectable models from the signed-in CLIs without inference calls.

Provider announcements don't imply account or CLI access. The native catalog is
our authority; failed refreshes retain bounded, explicitly stale observations.
"""
import json
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

TTL = 900
MAX_STALE = 86400
_cache: dict[str, dict] = {}
_locks = {e: threading.Lock() for e in ('claude', 'codex', 'gemini', 'qwen')}


def _rpc(engine: str) -> list[dict]:
    commands = {
        'codex': ['codex', 'app-server'],
        'claude': ['claude', '--input-format', 'stream-json', '--output-format',
                   'stream-json', '--verbose', '--strict-mcp-config', '--tools',
                   '', '--no-session-persistence'],
        'gemini': ['gemini', '--acp'],
    }
    # An isolated cwd avoids loading any project's files or instructions.
    with tempfile.TemporaryDirectory(prefix='gajala-models-') as cwd:
        p = subprocess.Popen(commands[engine], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             text=True, cwd=cwd)
        timer = threading.Timer(25, p.kill)
        timer.start()
        try:
            def send(msg):
                p.stdin.write(json.dumps(msg) + '\n')
                p.stdin.flush()

            def response(request_id):
                for line in p.stdout:
                    data = json.loads(line)
                    if engine == 'claude':
                        reply = data.get('response', {})
                        if reply.get('request_id') == request_id:
                            if reply.get('subtype') != 'success':
                                raise RuntimeError('CLI catalog initialization rejected')
                            return reply.get('response', {})
                    elif data.get('id') == request_id:
                        if 'error' in data:
                            raise RuntimeError('CLI catalog request rejected')
                        return data['result']
                raise RuntimeError('CLI catalog timed out or exited')

            if engine == 'claude':
                send({'type': 'control_request', 'request_id': 'init',
                      'request': {'subtype': 'initialize'}})
                return [{'id': m['value'], 'label': m.get('displayName', m['value']),
                         'resolved_model': m.get('resolvedModel')}
                        for m in response('init').get('models', [])
                        if m.get('value') and m['value'] != 'default']
            params = ({'clientInfo': {'name': 'gajala', 'version': '1'}}
                      if engine == 'codex' else
                      {'protocolVersion': 1, 'clientCapabilities': {}})
            send({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': params})
            response(1)
            if engine == 'gemini':
                send({'jsonrpc': '2.0', 'id': 2, 'method': 'session/new',
                      'params': {'cwd': cwd, 'mcpServers': []}})
                return [{'id': m['modelId'], 'label': m.get('name', m['modelId'])}
                        for m in response(2).get('models', {}).get('availableModels', [])]
            send({'method': 'initialized'})
            rows = []
            cursor = None
            for request_id in range(2, 22):
                send({'id': request_id, 'method': 'model/list',
                      'params': {'cursor': cursor, 'includeHidden': False}})
                result = response(request_id)
                rows.extend({'id': m['model'], 'label': m.get('displayName', m['model'])}
                            for m in result.get('data', []) if not m.get('hidden'))
                cursor = result.get('nextCursor')
                if not cursor:
                    return rows
            raise RuntimeError('CLI catalog exceeded pagination limit')
        finally:
            timer.cancel()
            if p.poll() is None:
                p.kill()
            p.communicate()


def _discover(engine: str) -> list[dict]:
    if engine != 'qwen':
        return _rpc(engine)
    import httpx
    from server import config
    r = httpx.get(config.OLLAMA_URL.rstrip('/') + '/api/tags', timeout=3)
    r.raise_for_status()
    return [{'id': m['name'], 'label': m['name']} for m in r.json().get('models', [])]


def get(engine: str, *, refresh: bool = False) -> dict:
    with _locks[engine]:
        now = time.time()
        previous = _cache.get(engine, {})
        if not refresh and previous.get('retry_at', 0) > now:
            return previous
        try:
            models = _discover(engine)
            if not models:
                raise RuntimeError('No selectable models reported')
            result = {'models': models, 'source': 'native CLI' if engine != 'qwen' else 'Ollama',
                      'updated_at': now, 'stale': False, 'error': None, 'retry_at': now + TTL}
        except Exception:
            models = (previous.get('models', []) if
                      now - previous.get('updated_at', 0) < MAX_STALE else [])
            result = {'models': models, 'source': previous.get('source', 'native CLI'),
                      'updated_at': previous.get('updated_at'), 'stale': True,
                      'error': 'Catalog refresh failed. Check CLI installation, sign-in and network.',
                      'retry_at': now + 60}
        _cache[engine] = result
        return result


def snapshot(*, refresh: bool = False) -> dict:
    engines = list(_locks)
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(lambda e: get(e, refresh=refresh), engines))
    return dict(zip(engines, values))
