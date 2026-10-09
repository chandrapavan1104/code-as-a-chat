"""Deliver background work into its source conversation, once across restarts.

The saved job is the durable outbox; conversation receipts atomically guard the
append. Delivery retries never re-run work and never guess a legacy origin.
"""
import hashlib
import logging
from server.db import night_queue_store, store

log = logging.getLogger(__name__)
TERMINAL = {"completed", "shipped", "deployed", "staged", "failed", "needs_you", "unverified", "stopped", "closed", "blocked", "awaiting_input"}


def deliver_pending_results() -> int:
    delivered = 0
    for job in night_queue_store.result_receipt_candidates():
        session = job.get("session_id")
        origin = job.get("origin_message_id")
        text = job.get("result_text")
        if not session or not origin or not text or job["status"] not in TERMINAL:
            continue
        source = store.get_message(origin, session)
        if source is None or source["role"] != "user":
            continue
        # Hash the immutable result body rather than status: shipping later must
        # not repost the same report. A later distinct attempt may deliver a new result.
        body = f"Work #{job['id']} · {job.get('result_completeness') or 'result'}\n\n{text}"
        receipt = f"queue-result:{job['id']}:{hashlib.sha256(body.encode()).hexdigest()[:24]}"
        try:
            if store.append_local_turn(session, receipt,
                    [{"role": "assistant", "content": body}], reply_to_message_id=origin):
                delivered += 1
            night_queue_store.mark_result_delivered(job["id"], job["result_saved_at"])
        except Exception:
            log.exception("Could not deliver result for work %s", job["id"])
    return delivered
