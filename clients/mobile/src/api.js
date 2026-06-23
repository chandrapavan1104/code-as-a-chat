// Thin client for the Code-as-a-Chat orchestrator.
// Same API the Telegram bot uses: POST /run, GET /skills, GET /health,
// all guarded by the X-API-Token header (except /health).

function base(url) {
  return url.replace(/\/+$/, '');
}

export async function checkHealth(serverUrl) {
  const r = await fetch(`${base(serverUrl)}/health`, { method: 'GET' });
  if (!r.ok) throw new Error(`health ${r.status}`);
  return r.json();
}

export async function fetchSkills(serverUrl, token) {
  const r = await fetch(`${base(serverUrl)}/skills`, {
    headers: { 'X-API-Token': token },
  });
  if (r.status === 401) throw new Error('Bad API token');
  if (!r.ok) throw new Error(`skills ${r.status}`);
  const data = await r.json();
  return data.skills || [];
}

export async function runCommand(serverUrl, token, command, prompt, sessionId, timeoutMs = 600000) {
  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const r = await fetch(`${base(serverUrl)}/run`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Token': token,
      },
      body: JSON.stringify({ command, prompt, session_id: sessionId }),
      signal: controller.signal,
    });
    if (r.status === 401) throw new Error('Bad API token');
    if (!r.ok) {
      const text = await r.text();
      throw new Error(`server ${r.status}: ${text.slice(0, 200)}`);
    }
    const data = await r.json();
    return data.result ?? '(no result)';
  } finally {
    clearTimeout(t);
  }
}
