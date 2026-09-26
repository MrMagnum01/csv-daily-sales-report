"""
Builds the daily sales report (HTML + CSV summary) and the exceptions
report (CSV) from a validated LoadResult.

All CSV-derived text is HTML-escaped before it reaches the HTML template --
untrusted input becoming markup is a real defect class in CSV-to-report
tools, not a hypothetical one; treat every field as hostile.

Money is never summed across currencies. Every revenue figure in this
module is grouped by currency first; there is no implicit FX conversion
anywhere in this file. If a client needs a single blended total, that
requires an explicit, dated exchange-rate source, which this demo does not
have and will not fake.
"""
import csv
import html
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
    to the day's own distribution, *within the same currency* -- a $500
    line and a EUR500 line are not on the same scale and must not be
    compared directly. Informational only -- outliers stay in the revenue
    totals, they're just called out for a human to glance at."""
    by_currency = defaultdict(list)
    for r in rows:
        if r.status == "completed":
            by_currency[r.currency].append(r)

    flagged = set()
    for currency, group in by_currency.items():
        totals = [r.line_total for r in group]
        if len(totals) < 2:
            continue
        mean = statistics.mean(totals)
        stdev = statistics.pstdev(totals)
        if stdev == 0:
            continue
        threshold = mean + OUTLIER_LINE_TOTAL_STDEV * stdev
        for r in group:
            if r.line_total >= max(threshold, OUTLIER_MIN_LINE_TOTAL):
                flagged.add(r.order_id)
    return flagged


def summarize(rows, outlier_ids):
    completed = [r for r in rows if r.status == "completed"]
    refunded = [r for r in rows if r.status == "refunded"]
    cancelled = [r for r in rows if r.status == "cancelled"]

    currencies = sorted({r.currency for r in rows})

    by_currency = defaultdict(lambda: {"gross_revenue": 0.0, "refund_amount": 0.0, "net_revenue": 0.0})
    for r in completed:
        by_currency[r.currency]["gross_revenue"] = round(
            by_currency[r.currency]["gross_revenue"] + r.line_total, 2
        )
    for r in refunded:
        by_currency[r.currency]["refund_amount"] = round(
            by_currency[r.currency]["refund_amount"] + r.line_total, 2
        )
    for currency, e in by_currency.items():
        e["net_revenue"] = round(e["gross_revenue"] - e["refund_amount"], 2)

    units_sold = sum(r.quantity for r in completed)  # a unit count, not money -- safe to sum across currencies

    # by_sku / by_region are keyed by (key, currency) -- never merged across
    # currency, so a revenue figure is always single-currency.
    by_sku = defaultdict(lambda: {"units": 0, "revenue": 0.0, "name": ""})
    by_region = defaultdict(lambda: {"units": 0, "revenue": 0.0})
    for r in completed:
        sku_key = (r.sku, r.currency)
        e = by_sku[sku_key]
        e["units"] += r.quantity
        e["revenue"] = round(e["revenue"] + r.line_total, 2)
        e["name"] = r.product_name or e["name"]

        reg = r.region or "unspecified"
        region_key = (reg, r.currency)
        e2 = by_region[region_key]
        e2["units"] += r.quantity
        e2["revenue"] = round(e2["revenue"] + r.line_total, 2)

    top_skus_by_currency = {}
    concentration_flags_by_currency = {}
    for currency in currencies:
        entries = [(sku, e) for (sku, cur), e in by_sku.items() if cur == currency]
        entries.sort(key=lambda kv: kv[1]["revenue"], reverse=True)
        top_skus_by_currency[currency] = entries[:10]

        gross = by_currency.get(currency, {}).get("gross_revenue", 0.0)
        flags = []
        if gross > 0:
            for sku, e in entries[:10]:
                share = e["revenue"] / gross
                if share >= TOP_SKU_CONCENTRATION_ALERT:
                    flags.append((sku, share))
        concentration_flags_by_currency[currency] = flags

    by_region_by_currency = defaultdict(dict)
    for (region, currency), e in by_region.items():
        by_region_by_currency[currency][region] = e

    # This tool operates at the ORDER LINE level throughout (one row per
    # SKU per order, per the input contract) -- these counts are line
    # counts, not order counts, and are named accordingly (Astra follow-up
    # finding 2: a 2-order/3-line fixture used to report "3 orders").
    # unique_orders_total is a separate, order-level count for reference.
    # Policy for an order whose lines have mixed status (e.g. one SKU
    # refunded, another still completed): this tool does NOT collapse an
    # order to a single status. Each line keeps its own status and is
    # counted/summed independently; there is no "the order's status is X"
    # decision made anywhere in this tool. See README.
    unique_orders_total = len({r.order_id for r in rows})

    return {
        "lines_total": len(rows),
        "lines_completed": len(completed),
        "lines_refunded": len(refunded),
        "lines_cancelled": len(cancelled),
        "unique_orders_total": unique_orders_total,
        "units_sold": units_sold,
        "currencies": currencies,
        "by_currency": dict(by_currency),
        "top_skus_by_currency": top_skus_by_currency,
        "by_region_by_currency": dict(by_region_by_currency),
        "outlier_ids": outlier_ids,
        "concentration_flags_by_currency": concentration_flags_by_currency,
    }


def write_csv_summary(path, report_date, summary):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["report_date", report_date])
        w.writerow(["lines_total", summary["lines_total"]])
        w.writerow(["lines_completed", summary["lines_completed"]])
        w.writerow(["lines_refunded", summary["lines_refunded"]])
        w.writerow(["lines_cancelled", summary["lines_cancelled"]])
        w.writerow(["unique_orders_total", summary["unique_orders_total"]])
        w.writerow(["units_sold", summary["units_sold"]])
        w.writerow([])
        w.writerow(["currency", "gross_revenue", "refund_amount", "net_revenue"])
        for currency in summary["currencies"]:
            e = summary["by_currency"].get(currency, {"gross_revenue": 0, "refund_amount": 0, "net_revenue": 0})
            w.writerow([currency, e["gross_revenue"], e["refund_amount"], e["net_revenue"]])
        w.writerow([])
        w.writerow(["currency", "sku", "product_name", "units", "revenue"])
        for currency in summary["currencies"]:
            for sku, e in summary["top_skus_by_currency"].get(currency, []):
                w.writerow([currency, sku, e["name"], e["units"], e["revenue"]])


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


def _fmt_money(currency, amount) -> str:
    return f"{_esc(currency)} {amount:.2f}"


def render_html(report_date, summary, load_result, generated_at=None):
    generated_at = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    currencies = summary["currencies"]

    kpi_currency_rows = "".join(
        f"<div class='kpi'><div class='label'>Gross ({_esc(cur)})</div>"
        f"<div class='value'>{_fmt_money(cur, summary['by_currency'].get(cur, {}).get('gross_revenue', 0.0))}</div></div>"
        for cur in currencies
    ) or "<div class='kpi'><div class='label'>Gross revenue</div><div class='value'>—</div></div>"

    currency_table_rows = "\n".join(
        f"<tr><td>{_esc(cur)}</td>"
        f"<td>{summary['by_currency'].get(cur, {}).get('gross_revenue', 0.0):.2f}</td>"
        f"<td>{summary['by_currency'].get(cur, {}).get('refund_amount', 0.0):.2f}</td>"
        f"<td>{summary['by_currency'].get(cur, {}).get('net_revenue', 0.0):.2f}</td></tr>"
        for cur in currencies
    ) or "<tr><td colspan=4>No completed orders</td></tr>"

    sku_sections = ""
    for cur in currencies:
        entries = summary["top_skus_by_currency"].get(cur, [])
        rows_html = "\n".join(
            f"<tr><td>{_esc(sku)}</td><td>{_esc(e['name'] or '—')}</td>"
            f"<td>{_esc(e['units'])}</td><td>{e['revenue']:.2f}</td></tr>"
            for sku, e in entries
        ) or "<tr><td colspan=4>No completed orders</td></tr>"
        sku_sections += (
            f"<h3>Currency: {_esc(cur)}</h3>"
            f"<table><tr><th>SKU</th><th>Product</th><th>Units</th><th>Revenue ({_esc(cur)})</th></tr>{rows_html}</table>"
        )
    if not currencies:
        sku_sections = "<p>No completed orders.</p>"

    region_sections = ""
    for cur in currencies:
        by_region = summary["by_region_by_currency"].get(cur, {})
        rows_html = "\n".join(
            f"<tr><td>{_esc(region)}</td><td>{_esc(e['units'])}</td><td>{e['revenue']:.2f}</td></tr>"
            for region, e in sorted(by_region.items(), key=lambda kv: -kv[1]["revenue"])
        ) or "<tr><td colspan=3>No completed orders</td></tr>"
        region_sections += (
            f"<h3>Currency: {_esc(cur)}</h3>"
            f"<table><tr><th>Region</th><th>Units</th><th>Revenue ({_esc(cur)})</th></tr>{rows_html}</table>"
        )
    if not currencies:
        region_sections = "<p>No completed orders.</p>"

    outlier_html = ""
    if summary["outlier_ids"]:
        items = "".join(f"<li>order {_esc(oid)}</li>" for oid in sorted(summary["outlier_ids"]))
        outlier_html = f"<div class='flag'><strong>Outlier line totals flagged for review (within their own currency):</strong><ul>{items}</ul></div>"

    concentration_html = ""
    all_flags = [
        (cur, sku, share)
        for cur, flags in summary["concentration_flags_by_currency"].items()
        for sku, share in flags
    ]
    if all_flags:
        items = "".join(
            f"<li>{_esc(sku)} ({_esc(cur)}): {share*100:.0f}% of that currency's gross revenue</li>"
            for cur, sku, share in all_flags
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
  h3 {{ margin-top: 1.2rem; color: #444; }}
  .note {{ color: #666; font-size: 0.85rem; }}
</style>
</head>
<body>
<h1>Daily Sales Report</h1>
<div class="meta">Report date: {_esc(report_date)} &middot; generated {_esc(generated_at)}</div>

{no_data_html}

<div class="kpis">
  <div class="kpi"><div class="label">Order lines</div><div class="value">{summary['lines_total']}</div></div>
  <div class="kpi"><div class="label">Unique orders</div><div class="value">{summary['unique_orders_total']}</div></div>
  <div class="kpi"><div class="label">Units sold</div><div class="value">{summary['units_sold']}</div></div>
  {kpi_currency_rows}
</div>
<p class="note">Revenue is reported per currency and never summed across currencies -- no exchange rate is assumed.</p>
<p class="note">Counts above are order LINES (one per SKU per order), not orders -- an order with 3 SKU lines counts as 3 lines and 1 unique order. Lines are never collapsed to a single per-order status: an order whose lines have different statuses (e.g. one SKU refunded, another still completed) keeps each line's own status.</p>

{outlier_html}
{concentration_html}
{bad_rows_html}
{quarantine_html}

<h2>Revenue by currency</h2>
<table>
<tr><th>Currency</th><th>Gross revenue</th><th>Refunds</th><th>Net revenue</th></tr>
{currency_table_rows}
</table>

<h2>Top products by revenue</h2>
{sku_sections}

<h2>Revenue by region</h2>
{region_sections}

</body>
</html>
"""
