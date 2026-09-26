#!/usr/bin/env python3
"""
CLI entry point: load one day of orders, validate, render the HTML report +
CSV summary + exceptions CSV, and fire failure alerts.

Exit codes: 0 = report generated AND every alert that fired was
successfully delivered (even if there were data exceptions -- the report
itself documents those, that's not a delivery failure). Non-zero = either
the run itself failed (no input file found at all, or an unhandled error),
or the report was generated but one or more alerts failed to deliver --
in both cases this is what run_daily.sh's retry loop watches for, so a
failed notification gets retried rather than silently swallowed. A failed
delivery is never reported as "OK".
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
    # that were already reported. Every fire() call's Boolean result is
    # tracked: a failed delivery must make the whole run non-zero so
    # run_daily.sh's retry loop actually retries it, instead of the CLI
    # printing OK while a real alert silently never went out.
    delivery_failures = []

    if result.file_empty:
        if not fire(report_date, "no_orders", "warning",
                     f"No usable orders found for {report_date} -- check the export job."):
            delivery_failures.append("no_orders")

    if result.total_bad_rows() > 0:
        if not fire(report_date, "malformed_rows", "warning",
                     f"{result.total_bad_rows()} malformed row(s) skipped on {report_date}: "
                     f"{dict(result.bad_row_categories)}"):
            delivery_failures.append("malformed_rows")

    if outlier_ids:
        if not fire(report_date, "revenue_outliers", "info",
                     f"{len(outlier_ids)} order(s) flagged as revenue outliers on {report_date}."):
            delivery_failures.append("revenue_outliers")

    if delivery_failures:
        print(
            f"PARTIAL: report generated for {report_date} -> {html_path}, "
            f"but alert delivery FAILED for: {', '.join(delivery_failures)}",
            file=sys.stderr,
        )
        return 3

    print(f"OK: report for {report_date} -> {html_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
