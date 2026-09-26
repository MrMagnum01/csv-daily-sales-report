#!/usr/bin/env bash
# run_daily.sh — scheduled-run wrapper around generate_report.py.
#
# Responsibilities beyond generate_report.py itself:
#   - always run through this tool's own venv (no assumption about the
#     caller's shell/PATH under cron or systemd)
#   - retry a transient failure (input not mounted yet, file momentarily
#     locked by an upstream writer) a few times with backoff before
#     escalating
#   - refuse to run concurrently with itself (flock) -- a manual rerun
#     overlapping the scheduled timer, or the timer firing again while a
#     previous slow run is still retrying, must not duplicate the report
#     or the alerts
#
# Configure via env vars: SALES_INPUT, SALES_OUTPUT_DIR, SALES_DATE,
# SALES_RETRY_ATTEMPTS, SALES_RETRY_BACKOFF_SECONDS, SALES_LOCK_FILE,
# SALES_PYTHON, SALES_ALERTS_LOG, NOTIFY_CMD.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

INPUT="${SALES_INPUT:-$SCRIPT_DIR/../data/sample/orders.csv}"
OUTPUT_DIR="${SALES_OUTPUT_DIR:-$SCRIPT_DIR/../output}"
ALERTS_LOG="${SALES_ALERTS_LOG:-$SCRIPT_DIR/alerts.log}"
LOCK_FILE="${SALES_LOCK_FILE:-$SCRIPT_DIR/.sales-report.lock}"
PY="${SALES_PYTHON:-$SCRIPT_DIR/../.venv/bin/python3}"

DATE_ARGS=()
if [ -n "${SALES_DATE:-}" ]; then
    DATE_ARGS=(--date "$SALES_DATE")
fi

# Idempotency guard: skip cleanly instead of racing/duplicating if another
# run is already in flight. -n = don't block, fail fast.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "${TS} [WARNING] run_daily.sh: another run is already in progress (lock held at $LOCK_FILE) -- skipping" >> "$ALERTS_LOG"
    exit 0
fi

RETRY_ATTEMPTS="${SALES_RETRY_ATTEMPTS:-3}"
IFS=',' read -r -a BACKOFFS <<< "${SALES_RETRY_BACKOFF_SECONDS:-5,15,30}"

if [ ! -x "$PY" ]; then
    TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "${TS} [CRITICAL] run_daily.sh: venv python not found/executable at $PY -- report NOT run" >> "$ALERTS_LOG"
    exit 1
fi

attempt=1
exit_code=1
while [ "$attempt" -le "$RETRY_ATTEMPTS" ]; do
    "$PY" generate_report.py --input "$INPUT" --output-dir "$OUTPUT_DIR" "${DATE_ARGS[@]}"
    exit_code=$?

    if [ $exit_code -eq 0 ]; then
        break
    fi

    if [ "$attempt" -lt "$RETRY_ATTEMPTS" ]; then
        idx=$((attempt - 1))
        if [ "$idx" -ge "${#BACKOFFS[@]}" ]; then
            idx=$((${#BACKOFFS[@]} - 1))
        fi
        backoff="${BACKOFFS[$idx]}"
        TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        echo "${TS} [INFO] run_daily.sh: attempt ${attempt}/${RETRY_ATTEMPTS} failed (exit ${exit_code}) -- retrying in ${backoff}s" >> "$ALERTS_LOG"
        sleep "$backoff"
    fi

    attempt=$((attempt + 1))
done

if [ $exit_code -ne 0 ]; then
    TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "${TS} [CRITICAL] run_daily.sh: generate_report.py failed after ${RETRY_ATTEMPTS} attempt(s), exit ${exit_code}" >> "$ALERTS_LOG"
fi

exit $exit_code
