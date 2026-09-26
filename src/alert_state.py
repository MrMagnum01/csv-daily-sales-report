"""
Per-day alert delivery state, so a retried or manually-rerun report on the
same date does not re-fire notifications that already succeeded.

State file: <state_dir>/alerts-<report_date>.json, a JSON object of
{event_id: delivered_at_iso}. Written atomically (temp file + os.replace)
so a crash mid-write can't corrupt it.
"""
import json
import os
import tempfile
from datetime import datetime, timezone

from config import get_state_dir


def _state_path(report_date: str) -> str:
    return os.path.join(get_state_dir(), f"alerts-{report_date}.json")


def _load(report_date: str) -> dict:
    path = _state_path(report_date)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        # Corrupt/partial state file: treat as empty rather than crashing
        # the run. Worst case is one duplicate notification, not a dead
        # report.
        return {}


def already_delivered(report_date: str, event_id: str) -> bool:
    return event_id in _load(report_date)


def mark_delivered(report_date: str, event_id: str) -> None:
    state_dir = get_state_dir()
    os.makedirs(state_dir, exist_ok=True)
    state = _load(report_date)
    state[event_id] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    path = _state_path(report_date)
    fd, tmp_path = tempfile.mkstemp(dir=state_dir, prefix=".alerts-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
