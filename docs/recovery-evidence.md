# Scheduled recovery evidence

A real `systemd --user` timer ran this repo's `src/run_daily.sh` on this
box for ~9 minutes (2026-09-26 13:08–13:16 UTC), with a forced failure, a
recovery, and a simulated scheduler restart, against a controlled local
notification receiver (`rehearsal-notify.sh`, appends the JSON payload it
receives to a log; never contacts anything external). This closes the
"real scheduled failure/recovery and restart evidence" gap Astra flagged
as still open after the code-level fixes.

This was throwaway infrastructure: a temporary timer/service pair under
`~/.config/systemd/user/`, firing every 50s, pointed at a
`rehearsal-input/`, `rehearsal-output/`, `rehearsal-state/` set of
directories separate from the checked-in `data/sample/`. **Both unit
files were deleted and the timer stopped/disabled at the end of the
rehearsal; nothing was left running or installed.** Paths below are
sanitized (`/opt/csv-daily-sales-report/...` in place of this box's real
checkout path; hostname replaced with `rehearsal-host`).

## Timeline

| Phase | What happened |
|---|---|
| 13:08:06 | Timer started. `rehearsal-input/orders.csv` deliberately absent (forced failure). |
| 13:08:12 – 13:09:57 | Three separate timer firings (~13:08:12, 13:09:03, 13:09:54), each: `run_daily.sh` retries once (3s backoff, `SALES_RETRY_ATTEMPTS=2`), still fails (`exit 2`, input missing), logs `CRITICAL`. |
| 13:10:36 | Recovery: real fixed-seed `data/sample/orders.csv` copied into `rehearsal-input/orders.csv`. No code/service change. |
| 13:10:45 onward | Next and all subsequent firings: `OK: report for 2026-01-15`. |
| 13:13:10 | **Simulated scheduler restart**: `systemctl --user stop` then `start` on the timer (stand-in for a host reboot / service restart), mid-rehearsal. |
| 13:13:15 – 13:15:48 | Firings continue post-restart, every ~50s, all `OK`. |
| 13:16:10 | Timer stopped, disabled, unit files deleted, `daemon-reload`. |

Full sanitized excerpts: [`recovery-evidence-journal.log`](recovery-evidence-journal.log) (systemd journal, one line per invocation), [`recovery-evidence-alerts.log`](recovery-evidence-alerts.log) (`run_daily.sh`'s own retry/escalation log), [`recovery-evidence-notify-received.log`](recovery-evidence-notify-received.log) (everything the controlled receiver got).

## What failed

Input file missing (`rehearsal-input/orders.csv` did not exist). `generate_report.py` exits 2; `run_daily.sh` retries once after 3s, still fails, logs `[CRITICAL] ... failed after 2 attempt(s), exit 2` to `alerts.log`, and (on the first occurrence only — see idempotency below) delivers an `input_missing` critical alert to the receiver.

## What retried

- **Within one run**: `run_daily.sh`'s own retry loop (`SALES_RETRY_ATTEMPTS=2`, 3s backoff) — visible as one `[INFO] attempt 1/2 failed ... retrying in 3s` line per firing in `recovery-evidence-alerts.log`.
- **Across runs**: the timer itself re-fired every ~50s and re-attempted the whole job three times during the outage (13:08:12, 13:09:03, 13:09:54) before the input was fixed — this is the "scheduled retry" layer above the wrapper's own retry.
- **Across a restart**: after the simulated `systemctl --user stop`/`start` at 13:13:10, the timer resumed firing on schedule and kept succeeding — state (`rehearsal-state/`) is a plain file on disk, unaffected by the restart.

## Idempotent output check

- **No duplicate alerts despite 3 separate failed firings**: `recovery-evidence-notify-received.log` shows exactly **one** `input_missing` delivery (13:08:12), not three, even though the underlying failure recurred at 13:08:12, 13:09:03, and 13:09:54. This is `alert_state.py`'s per-`(report_date, event_id)` dedup working as designed against a real repeated scheduled failure, not just the unit test that calls `alert.fire()` twice in-process.
- **No duplicate alerts across 7 successful post-recovery runs, including across the restart**: `malformed_rows` and `revenue_outliers` (from the fixed-seed sample data's deliberately-planted exceptions) each appear exactly **once** in the receiver log, at 13:10:44–13:10:45, despite 7 successful report runs in total (13:10:45 through 13:15:48, spanning the 13:13:10 restart).
- **Deterministic report content**: re-ran `generate_report.py` twice in a row against the same rehearsal input/date immediately after the timed rehearsal and diffed both HTML reports with the `generated <timestamp>` line stripped — byte-identical. Confirms a rerun of the same date does not change the report's substantive content, only its generation timestamp.

## What this does and does not establish

Establishes: the real scheduler (systemd timer, not just the wrapper script in isolation) retries a genuine input-missing failure, escalates to `CRITICAL` after exhausting retries, recovers cleanly once the input appears with no code change, survives a scheduler restart without duplicating alerts or corrupting state, and produces deterministic report output across reruns.

Does not establish: behavior against a *real* notification channel (the receiver here is a local script, not email/Slack/PagerDuty), behavior over a much longer unattended period (this was ~9 minutes, not days), or anything about client-specific data/volume. Support/SLA scope is unchanged from the README.
