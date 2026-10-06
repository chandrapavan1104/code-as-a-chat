"""Multiple-choice questions the agent attaches to a reply.

The agent ends a reply with [[ask:{"question": ..., "options": [...],
"multi": false}]] when it needs the owner to pick between concrete options.
The app renders it as tappable choices; plain-text clients (Telegram, /run)
get a numbered list instead. Malformed blocks are dropped rather than shown.
"""

import json
import re

ASK = re.compile(r"\[\[ask:(\{.*?\})\]\]", re.S)
MAX_OPTIONS = 6


def _parse(raw: str) -> dict | None:
    try:
        card = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(card, dict):
        return None
    question = str(card.get("question") or "").strip()
    options = [str(o).strip()[:80] for o in card.get("options") or [] if str(o).strip()]
    options = list(dict.fromkeys(options))  # no duplicate choices
    if not question or not 2 <= len(options) <= MAX_OPTIONS:
        return None
    return {"question": question[:300], "options": options,
            "multi": bool(card.get("multi"))}


def normalize(text: str) -> str:
    """Keep at most one valid card, at the end; drop malformed ones."""
    cards = [c for c in (_parse(m.group(1)) for m in ASK.finditer(text or "")) if c]
    body = ASK.sub("", text or "").rstrip()
    if not cards:
        return body
    return f"{body}\n\n[[ask:{json.dumps(cards[-1], ensure_ascii=False)}]]"


def as_plain_text(text: str) -> str:
    """For clients that cannot render a card."""
    def render(m: re.Match) -> str:
        card = _parse(m.group(1))
        if not card:
            return ""
        lines = [card["question"] + (" (pick any)" if card["multi"] else "")]
        lines += [f"{i}. {o}" for i, o in enumerate(card["options"], 1)]
        return "\n".join(lines)
    return ASK.sub(render, text or "").rstrip()
