"""
Failure-alert hook.

This is a STUB: it never contacts a real endpoint. It prints exactly what
it would have sent, so a client can see the payload shape and wire in their
own channel (email, Slack webhook, PagerDuty, etc.) by replacing `_send`.

Delivery-state tracking (alert_state.py) makes repeated notification
attempts idempotent: the same (report_date, event_id) pair is not
re-delivered on a rerun of the same day, but a genuinely new event
(different id) always gets its own attempt.
"""
import json
import os
import sys
from datetime import datetime, timezone

from alert_state import already_delivered, mark_delivered

NOTIFY_CMD = os.environ.get("NOTIFY_CMD")  # optional: shell out to a real sender


def _send(event: dict) -> bool:
    """Delivery stub. Returns True on 'success'. Replace this function (or
    set NOTIFY_CMD to an executable that reads JSON on stdin) to wire a
    real channel. Never raises -- a broken notifier must not crash the
    report run; it must be visible as a failed delivery instead."""
    payload = json.dumps(event, sort_keys=True)
    if NOTIFY_CMD:
        import subprocess
        try:
            proc = subprocess.run(
                NOTIFY_CMD, input=payload, text=True, shell=True,
                capture_output=True, timeout=15,
            )
            if proc.returncode != 0:
                print(
                    f"[alert] NOTIFY_CMD exited {proc.returncode}: {proc.stderr.strip()}",
                    file=sys.stderr,
                )
                return False
            return True
        except Exception as exc:  # noqa: BLE001 -- notifier failure must never crash the run
            print(f"[alert] NOTIFY_CMD raised: {exc}", file=sys.stderr)
            return False

    print(f"[alert-stub] would send: {payload}")
    return True


def fire(report_date: str, event_id: str, severity: str, message: str) -> bool:
    """Send one alert event, deduplicated by (report_date, event_id).

    Returns True if the event was delivered (now, or already delivered on a
    prior run of the same day -- both count as "handled"). Returns False
    only when delivery was attempted and failed, so the caller can decide
    whether that should affect the process exit code.
    """
    if already_delivered(report_date, event_id):
        return True

    event = {
        "event_id": event_id,
        "report_date": report_date,
        "severity": severity,
        "message": message,
        "fired_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    ok = _send(event)
    if ok:
        mark_delivered(report_date, event_id)
    return ok
