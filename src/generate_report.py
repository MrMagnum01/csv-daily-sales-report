#!/usr/bin/env python3
"""
CLI entry point: load one day of orders, validate, render the HTML report +
CSV summary + exceptions CSV, and fire failure alerts.

Exit codes: 0 = report generated (even if there were exceptions -- the
report itself documents those). Non-zero = the run itself failed (no input
file found at all, or an unhandled error) -- this is what run_daily.sh's
retry loop watches for.
"""
import argparse
import os
import sys
from datetime import datetime, timezone

from alert import fire
from data_loader import load_orders
from report import compute_outliers, render_html, summarize, write_csv_summary, write_exceptions_csv


def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", required=True, help="path to the orders CSV for the day")
    p.add_argument("--output-dir", required=True, help="directory to write report/ files into")
    p.add_argument("--date", default=None, help="report date YYYY-MM-DD (default: today, UTC)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv or sys.argv[1:])
    report_date = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    os.makedirs(args.output_dir, exist_ok=True)

    result = load_orders(args.input, report_date)

    if not result.file_present:
        fire(report_date, "input_missing", "critical",
             f"Input file not found: {args.input}")
        print(f"ERROR: input file not found: {args.input}", file=sys.stderr)
        return 2

    outlier_ids = compute_outliers(result.rows)
    summary = summarize(result.rows, outlier_ids)

    html_path = os.path.join(args.output_dir, f"daily-sales-report-{report_date}.html")
    csv_path = os.path.join(args.output_dir, f"daily-sales-summary-{report_date}.csv")
    exceptions_path = os.path.join(args.output_dir, f"exceptions-{report_date}.csv")

    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(render_html(report_date, summary, result))
    write_csv_summary(csv_path, report_date, summary)
    write_exceptions_csv(exceptions_path, result)

    # Alerting -- each condition is its own event id, deduplicated per day
    # so a retry/rerun of the same date doesn't re-notify on conditions
    # that were already reported.
    if result.file_empty:
        fire(report_date, "no_orders", "warning",
             f"No usable orders found for {report_date} -- check the export job.")

    if result.total_bad_rows() > 0:
        fire(report_date, "malformed_rows", "warning",
             f"{result.total_bad_rows()} malformed row(s) skipped on {report_date}: "
             f"{dict(result.bad_row_categories)}")

    if outlier_ids:
        fire(report_date, "revenue_outliers", "info",
             f"{len(outlier_ids)} order(s) flagged as revenue outliers on {report_date}.")

    print(f"OK: report for {report_date} -> {html_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
