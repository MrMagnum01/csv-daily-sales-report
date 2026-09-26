import csv
import json
import os
import subprocess
import sys
import tempfile

import pytest

from alert import fire
from alert_state import already_delivered
from data_loader import (
    CAT_BAD_DATE,
    CAT_DUPLICATE,
    CAT_MISSING_FIELD,
    CAT_NON_FINITE_PRICE,
    CAT_NON_NUMERIC_QTY,
    CAT_UNKNOWN_STATUS,
    CAT_WRONG_DAY,
    load_orders,
)
from report import compute_outliers, render_html, summarize, write_csv_summary, write_exceptions_csv

REPORT_DATE = "2026-01-15"

HEADER = "order_id,order_date,customer_id,sku,product_name,category,quantity,unit_price,status,region,currency\n"


def _write_csv(tmp_path, rows):
    path = os.path.join(tmp_path, "orders.csv")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(HEADER)
        fh.write(rows)
    return path


def test_missing_file_reports_not_present(tmp_path):
    result = load_orders(str(tmp_path / "nope.csv"), REPORT_DATE)
    assert result.file_present is False
    assert result.rows == []


def test_empty_file_reports_empty(tmp_path):
    path = tmp_path / "orders.csv"
    path.write_text(HEADER)
    result = load_orders(str(path), REPORT_DATE)
    assert result.file_present is True
    assert result.file_empty is True


def test_valid_row_parses(tmp_path):
    row = "ORD-1,2026-01-15,CUST-1,SKU-1001,Lamp,Home,2,10.00,completed,europe,USD\n"
    path = _write_csv(tmp_path, row)
    result = load_orders(path, REPORT_DATE)
    assert len(result.rows) == 1
    assert result.rows[0].line_total == 20.00


def test_missing_required_field_is_bad_row(tmp_path):
    row = "ORD-1,2026-01-15,,SKU-1001,Lamp,Home,2,10.00,completed,europe,USD\n"
    path = _write_csv(tmp_path, row)
    result = load_orders(path, REPORT_DATE)
    assert result.rows == []
    assert result.bad_row_categories[CAT_MISSING_FIELD] == 1


def test_bad_date_is_bad_row(tmp_path):
    row = "ORD-1,not-a-date,CUST-1,SKU-1001,Lamp,Home,2,10.00,completed,europe,USD\n"
    path = _write_csv(tmp_path, row)
    result = load_orders(path, REPORT_DATE)
    assert result.bad_row_categories[CAT_BAD_DATE] == 1


def test_non_numeric_quantity_is_bad_row(tmp_path):
    row = "ORD-1,2026-01-15,CUST-1,SKU-1001,Lamp,Home,many,10.00,completed,europe,USD\n"
    path = _write_csv(tmp_path, row)
    result = load_orders(path, REPORT_DATE)
    assert result.bad_row_categories[CAT_NON_NUMERIC_QTY] == 1


def test_negative_price_is_bad_row(tmp_path):
    row = "ORD-1,2026-01-15,CUST-1,SKU-1001,Lamp,Home,1,-5.00,completed,europe,USD\n"
    path = _write_csv(tmp_path, row)
    result = load_orders(path, REPORT_DATE)
    assert result.bad_row_categories[CAT_NON_FINITE_PRICE] == 1


def test_unknown_status_is_bad_row(tmp_path):
    row = "ORD-1,2026-01-15,CUST-1,SKU-1001,Lamp,Home,1,10.00,backordered,europe,USD\n"
    path = _write_csv(tmp_path, row)
    result = load_orders(path, REPORT_DATE)
    assert result.bad_row_categories[CAT_UNKNOWN_STATUS] == 1


def test_wrong_day_row_is_quarantined_not_dropped_silently(tmp_path):
    row = "ORD-1,2026-01-14,CUST-1,SKU-1001,Lamp,Home,1,10.00,completed,europe,USD\n"
    path = _write_csv(tmp_path, row)
    result = load_orders(path, REPORT_DATE)
    assert result.rows == []
    assert len(result.quarantined) == 1
    assert result.quarantined[0].category == CAT_WRONG_DAY


def test_duplicate_order_id_is_quarantined_keeps_first(tmp_path):
    rows = (
        "ORD-1,2026-01-15,CUST-1,SKU-1001,Lamp,Home,1,10.00,completed,europe,USD\n"
        "ORD-1,2026-01-15,CUST-2,SKU-1002,Bag,Bags,1,20.00,completed,europe,USD\n"
    )
    path = _write_csv(tmp_path, rows)
    result = load_orders(path, REPORT_DATE)
    assert len(result.rows) == 1
    assert result.rows[0].customer_id == "CUST-1"
    assert len(result.quarantined) == 1
    assert result.quarantined[0].category == CAT_DUPLICATE


def test_outlier_detection_flags_large_line_total(tmp_path):
    rows = "".join(
        f"ORD-{i},2026-01-15,CUST-{i},SKU-1001,Lamp,Home,1,10.00,completed,europe,USD\n"
        for i in range(10)
    )
    rows += "ORD-99,2026-01-15,CUST-99,SKU-1006,Speaker,Electronics,50,60.00,completed,europe,USD\n"
    path = _write_csv(tmp_path, rows)
    result = load_orders(path, REPORT_DATE)
    outliers = compute_outliers(result.rows)
    assert "ORD-99" in outliers
    assert "ORD-0" not in outliers


def test_summarize_computes_net_revenue_and_top_skus(tmp_path):
    rows = (
        "ORD-1,2026-01-15,CUST-1,SKU-1001,Lamp,Home,2,10.00,completed,europe,USD\n"
        "ORD-2,2026-01-15,CUST-2,SKU-1001,Lamp,Home,1,10.00,refunded,europe,USD\n"
        "ORD-3,2026-01-15,CUST-3,SKU-1002,Bag,Bags,1,50.00,completed,apac,USD\n"
    )
    path = _write_csv(tmp_path, rows)
    result = load_orders(path, REPORT_DATE)
    summary = summarize(result.rows, set())
    assert summary["gross_revenue"] == 70.00
    assert summary["refund_amount"] == 10.00
    assert summary["net_revenue"] == 60.00
    assert summary["top_skus"][0][0] == "SKU-1002"


def test_html_escaping_of_untrusted_csv_values(tmp_path):
    payload = "<script>alert(1)</script>"
    row = f'ORD-1,2026-01-15,CUST-1,SKU-1001,"{payload}",Home,1,10.00,completed,europe,USD\n'
    path = _write_csv(tmp_path, row)
    result = load_orders(path, REPORT_DATE)
    summary = summarize(result.rows, set())
    out = render_html(REPORT_DATE, summary, result)
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out


def test_csv_summary_and_exceptions_written(tmp_path):
    rows = (
        "ORD-1,2026-01-15,CUST-1,SKU-1001,Lamp,Home,1,10.00,completed,europe,USD\n"
        "ORD-2,bad-date,CUST-2,SKU-1002,Bag,Bags,1,10.00,completed,europe,USD\n"
    )
    path = _write_csv(tmp_path, rows)
    result = load_orders(path, REPORT_DATE)
    summary = summarize(result.rows, set())

    csv_out = tmp_path / "summary.csv"
    exc_out = tmp_path / "exceptions.csv"
    write_csv_summary(str(csv_out), REPORT_DATE, summary)
    write_exceptions_csv(str(exc_out), result)

    assert csv_out.exists()
    with open(exc_out) as fh:
        exc_rows = list(csv.reader(fh))
    assert any("bad_date" in r for r in exc_rows)


def test_alert_fires_and_is_idempotent_on_rerun(tmp_path, monkeypatch):
    monkeypatch.setenv("SALES_STATE_DIR", str(tmp_path / "state"))
    calls = []

    import alert as alert_mod
    monkeypatch.setattr(alert_mod, "_send", lambda event: calls.append(event) or True)

    ok1 = fire(REPORT_DATE, "no_orders", "warning", "first attempt")
    ok2 = fire(REPORT_DATE, "no_orders", "warning", "second attempt (rerun)")

    assert ok1 is True
    assert ok2 is True
    assert len(calls) == 1  # second call was deduplicated, not re-sent
    assert already_delivered(REPORT_DATE, "no_orders") is True


def test_alert_failure_returns_false_and_is_not_marked_delivered(tmp_path, monkeypatch):
    monkeypatch.setenv("SALES_STATE_DIR", str(tmp_path / "state"))
    import alert as alert_mod
    monkeypatch.setattr(alert_mod, "_send", lambda event: False)

    ok = fire(REPORT_DATE, "input_missing", "critical", "boom")
    assert ok is False
    assert already_delivered(REPORT_DATE, "input_missing") is False


def test_cli_end_to_end_writes_report_files(tmp_path):
    src_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    gen_script = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "sample", "generate_sample_data.py",
    )
    orders_csv = data_dir / "orders.csv"
    subprocess.run(
        [sys.executable, gen_script, "--out", str(orders_csv), "--date", REPORT_DATE, "--seed", "42"],
        check=True,
    )

    out_dir = tmp_path / "output"
    env = dict(os.environ, SALES_STATE_DIR=str(tmp_path / "state"))
    proc = subprocess.run(
        [sys.executable, os.path.join(src_dir, "generate_report.py"),
         "--input", str(orders_csv), "--output-dir", str(out_dir), "--date", REPORT_DATE],
        cwd=src_dir, env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert (out_dir / f"daily-sales-report-{REPORT_DATE}.html").exists()
    assert (out_dir / f"daily-sales-summary-{REPORT_DATE}.csv").exists()
    assert (out_dir / f"exceptions-{REPORT_DATE}.csv").exists()


def test_run_daily_skips_when_lock_held(tmp_path):
    src_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    lock_file = tmp_path / "held.lock"
    alerts_log = tmp_path / "alerts.log"

    # Hold the lock ourselves via flock in a background shell, then invoke
    # run_daily.sh and confirm it skips cleanly (exit 0, WARNING logged,
    # no report written) instead of racing.
    holder = subprocess.Popen(
        ["bash", "-c", f'exec 9>"{lock_file}"; flock 9; sleep 5'],
    )
    try:
        import time
        time.sleep(0.5)  # let the holder actually acquire the lock
        env = dict(
            os.environ,
            SALES_LOCK_FILE=str(lock_file),
            SALES_ALERTS_LOG=str(alerts_log),
            SALES_OUTPUT_DIR=str(tmp_path / "output"),
        )
        proc = subprocess.run(
            ["bash", os.path.join(src_dir, "run_daily.sh")],
            env=env, capture_output=True, text=True, timeout=20,
        )
        assert proc.returncode == 0
        assert "another run is already in progress" in alerts_log.read_text()
        assert not (tmp_path / "output").exists()
    finally:
        holder.kill()
        holder.wait()


def test_run_daily_retries_then_succeeds(tmp_path, monkeypatch):
    src_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    # No input file at all -> generate_report.py exits 2 every attempt.
    # Use fast backoff and confirm the CRITICAL escalation line appears
    # after all attempts are exhausted (proves the retry loop actually
    # looped, not just failed once).
    env = dict(
        os.environ,
        SALES_INPUT=str(tmp_path / "missing.csv"),
        SALES_OUTPUT_DIR=str(tmp_path / "output"),
        SALES_ALERTS_LOG=str(tmp_path / "alerts.log"),
        SALES_LOCK_FILE=str(tmp_path / "run.lock"),
        SALES_RETRY_ATTEMPTS="2",
        SALES_RETRY_BACKOFF_SECONDS="0",
        SALES_STATE_DIR=str(tmp_path / "state"),
    )
    proc = subprocess.run(
        ["bash", os.path.join(src_dir, "run_daily.sh")],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode != 0
    log = (tmp_path / "alerts.log").read_text()
    assert "attempt 1/2 failed" in log
    assert "CRITICAL" in log
