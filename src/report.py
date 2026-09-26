"""
Builds the daily sales report (HTML + CSV summary) and the exceptions
report (CSV) from a validated LoadResult.

All CSV-derived text is HTML-escaped before it reaches the HTML template --
untrusted input becoming markup is a real defect class in CSV-to-report
tools, not a hypothetical one; treat every field as hostile.
"""
import csv
import html
import math
import statistics
from collections import defaultdict
from datetime import datetime, timezone

from config import (
    OUTLIER_LINE_TOTAL_STDEV,
    OUTLIER_MIN_LINE_TOTAL,
    TOP_SKU_CONCENTRATION_ALERT,
)


def compute_outliers(rows):
    """Flag completed-order line totals that are unusually large relative
    to the day's own distribution. Informational only -- outliers stay in
    the revenue totals, they're just called out for a human to glance at."""
    totals = [r.line_total for r in rows if r.status == "completed"]
    if len(totals) < 2:
        return set()
    mean = statistics.mean(totals)
    stdev = statistics.pstdev(totals)
    if stdev == 0:
        return set()
    threshold = mean + OUTLIER_LINE_TOTAL_STDEV * stdev
    flagged = set()
    for r in rows:
        if r.status != "completed":
            continue
        if r.line_total >= max(threshold, OUTLIER_MIN_LINE_TOTAL):
            flagged.add(r.order_id)
    return flagged


def summarize(rows, outlier_ids):
    completed = [r for r in rows if r.status == "completed"]
    refunded = [r for r in rows if r.status == "refunded"]
    cancelled = [r for r in rows if r.status == "cancelled"]

    gross_revenue = round(sum(r.line_total for r in completed), 2)
    refund_amount = round(sum(r.line_total for r in refunded), 2)
    net_revenue = round(gross_revenue - refund_amount, 2)
    units_sold = sum(r.quantity for r in completed)

    by_sku = defaultdict(lambda: {"units": 0, "revenue": 0.0, "name": ""})
    by_region = defaultdict(lambda: {"units": 0, "revenue": 0.0})
    for r in completed:
        e = by_sku[r.sku]
        e["units"] += r.quantity
        e["revenue"] = round(e["revenue"] + r.line_total, 2)
        e["name"] = r.product_name or e["name"]
        reg = r.region or "unspecified"
        e2 = by_region[reg]
        e2["units"] += r.quantity
        e2["revenue"] = round(e2["revenue"] + r.line_total, 2)

    top_skus = sorted(by_sku.items(), key=lambda kv: kv[1]["revenue"], reverse=True)[:10]

    concentration_flags = []
    if gross_revenue > 0:
        for sku, e in top_skus:
            share = e["revenue"] / gross_revenue
            if share >= TOP_SKU_CONCENTRATION_ALERT:
                concentration_flags.append((sku, share))

    return {
        "orders_total": len(rows),
        "orders_completed": len(completed),
        "orders_refunded": len(refunded),
        "orders_cancelled": len(cancelled),
        "gross_revenue": gross_revenue,
        "refund_amount": refund_amount,
        "net_revenue": net_revenue,
        "units_sold": units_sold,
        "by_sku": by_sku,
        "by_region": dict(by_region),
        "top_skus": top_skus,
        "outlier_ids": outlier_ids,
        "concentration_flags": concentration_flags,
    }


def write_csv_summary(path, report_date, summary):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["report_date", report_date])
        w.writerow(["orders_total", summary["orders_total"]])
        w.writerow(["orders_completed", summary["orders_completed"]])
        w.writerow(["orders_refunded", summary["orders_refunded"]])
        w.writerow(["orders_cancelled", summary["orders_cancelled"]])
        w.writerow(["gross_revenue", summary["gross_revenue"]])
        w.writerow(["refund_amount", summary["refund_amount"]])
        w.writerow(["net_revenue", summary["net_revenue"]])
        w.writerow(["units_sold", summary["units_sold"]])
        w.writerow([])
        w.writerow(["sku", "product_name", "units", "revenue"])
        for sku, e in summary["top_skus"]:
            w.writerow([sku, e["name"], e["units"], e["revenue"]])


def write_exceptions_csv(path, load_result):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["kind", "category", "detail", "order_id", "raw"])
        for cat, count in sorted(load_result.bad_row_categories.items()):
            w.writerow(["bad_row_category_count", cat, count, "", ""])
        for q in load_result.quarantined:
            w.writerow([
                "quarantined", q.category, q.detail,
                q.raw.get("order_id", ""), dict(q.raw),
            ])


def _esc(v) -> str:
    return html.escape(str(v), quote=True)


def render_html(report_date, summary, load_result, generated_at=None):
    generated_at = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    sku_rows = "\n".join(
        f"<tr><td>{_esc(sku)}</td><td>{_esc(e['name'] or '—')}</td>"
        f"<td>{_esc(e['units'])}</td><td>${_esc(f'{e['revenue']:.2f}')}</td></tr>"
        for sku, e in summary["top_skus"]
    ) or "<tr><td colspan=4>No completed orders</td></tr>"

    region_rows = "\n".join(
        f"<tr><td>{_esc(region)}</td><td>{_esc(e['units'])}</td>"
        f"<td>${_esc(f'{e['revenue']:.2f}')}</td></tr>"
        for region, e in sorted(summary["by_region"].items(), key=lambda kv: -kv[1]["revenue"])
    ) or "<tr><td colspan=3>No completed orders</td></tr>"

    outlier_html = ""
    if summary["outlier_ids"]:
        items = "".join(f"<li>order {_esc(oid)}</li>" for oid in sorted(summary["outlier_ids"]))
        outlier_html = f"<div class='flag'><strong>Outlier line totals flagged for review:</strong><ul>{items}</ul></div>"

    concentration_html = ""
    if summary["concentration_flags"]:
        items = "".join(
            f"<li>{_esc(sku)}: {share*100:.0f}% of gross revenue</li>"
            for sku, share in summary["concentration_flags"]
        )
        concentration_html = f"<div class='flag'><strong>Revenue concentration flags:</strong><ul>{items}</ul></div>"

    bad_rows_html = ""
    if load_result.bad_row_categories:
        items = "".join(
            f"<li>{_esc(cat)}: {count}</li>"
            for cat, count in sorted(load_result.bad_row_categories.items())
        )
        bad_rows_html = f"<div class='flag warn'><strong>Malformed rows skipped:</strong><ul>{items}</ul></div>"

    quarantine_html = ""
    if load_result.quarantined:
        by_cat = defaultdict(int)
        for q in load_result.quarantined:
            by_cat[q.category] += 1
        items = "".join(f"<li>{_esc(cat)}: {count}</li>" for cat, count in sorted(by_cat.items()))
        quarantine_html = f"<div class='flag warn'><strong>Rows quarantined (excluded from totals):</strong><ul>{items}</ul></div>"

    no_data_html = ""
    if load_result.file_present and load_result.file_empty:
        no_data_html = "<div class='flag error'><strong>No orders found for this report date.</strong></div>"
    elif not load_result.file_present:
        no_data_html = "<div class='flag error'><strong>Input file was not found.</strong></div>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Daily Sales Report — {_esc(report_date)}</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 2rem; color: #1a1a1a; background: #fafafa; }}
  h1 {{ margin-bottom: 0.2rem; }}
  .meta {{ color: #666; font-size: 0.9rem; margin-bottom: 1.5rem; }}
  .kpis {{ display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.5rem; }}
  .kpi {{ background: white; border: 1px solid #ddd; border-radius: 8px; padding: 1rem 1.4rem; min-width: 140px; }}
  .kpi .label {{ font-size: 0.8rem; color: #666; text-transform: uppercase; letter-spacing: 0.03em; }}
  .kpi .value {{ font-size: 1.6rem; font-weight: 600; margin-top: 0.2rem; }}
  table {{ border-collapse: collapse; width: 100%; margin-bottom: 1.5rem; background: white; }}
  th, td {{ border: 1px solid #ddd; padding: 0.5rem 0.7rem; text-align: left; font-size: 0.92rem; }}
  th {{ background: #f0f0f0; }}
  .flag {{ background: #fff8e1; border: 1px solid #f0d878; border-radius: 6px; padding: 0.8rem 1rem; margin-bottom: 1rem; font-size: 0.9rem; }}
  .flag.warn {{ background: #fff3e0; border-color: #f0b878; }}
  .flag.error {{ background: #fdecea; border-color: #e57373; }}
  h2 {{ margin-top: 2rem; }}
</style>
</head>
<body>
<h1>Daily Sales Report</h1>
<div class="meta">Report date: {_esc(report_date)} &middot; generated {_esc(generated_at)}</div>

{no_data_html}

<div class="kpis">
  <div class="kpi"><div class="label">Orders</div><div class="value">{summary['orders_total']}</div></div>
  <div class="kpi"><div class="label">Gross revenue</div><div class="value">${summary['gross_revenue']:.2f}</div></div>
  <div class="kpi"><div class="label">Refunds</div><div class="value">${summary['refund_amount']:.2f}</div></div>
  <div class="kpi"><div class="label">Net revenue</div><div class="value">${summary['net_revenue']:.2f}</div></div>
  <div class="kpi"><div class="label">Units sold</div><div class="value">{summary['units_sold']}</div></div>
</div>

{outlier_html}
{concentration_html}
{bad_rows_html}
{quarantine_html}

<h2>Top products by revenue</h2>
<table>
<tr><th>SKU</th><th>Product</th><th>Units</th><th>Revenue</th></tr>
{sku_rows}
</table>

<h2>Revenue by region</h2>
<table>
<tr><th>Region</th><th>Units</th><th>Revenue</th></tr>
{region_rows}
</table>

</body>
</html>
"""
