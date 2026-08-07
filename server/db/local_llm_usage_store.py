"""Append exact Ollama token counts for Codaur and local dashboards.

Ollama returns prompt/eval counts with every non-streaming response but does not
keep a queryable usage history. Persisting that small response envelope at call
time makes local Qwen usage observable without saving prompts or replies.
"""

import datetime as dt
import json
import uuid
from pathlib import Path

DB_PATH = Path.home() / ".codeasachat" / "qwen_usage.jsonl"


def record(*, response: dict, model: str, cwd: str, source: str,
           session_id: str | None = None) -> None:
    prompt = int(response.get("prompt_eval_count") or 0)
    generated = int(response.get("eval_count") or 0)
    row = {
        "id": str(uuid.uuid4()),
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "model": model,
        "cwd": cwd,
        "source": source,
        "sessionId": session_id,
        "inputTokens": prompt,
        "outputTokens": generated,
        "totalTokens": prompt + generated,
        "totalDurationNs": int(response.get("total_duration") or 0),
        "evalDurationNs": int(response.get("eval_duration") or 0),
    }
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DB_PATH, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, separators=(",", ":")) + "\n")
