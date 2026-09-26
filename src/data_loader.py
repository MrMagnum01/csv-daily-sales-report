"""
Loads and validates one day of order-export CSV data.

Design (mirrors the client-facing README):
- Input: a single CSV of orders, one row per order line. Rows may span more
  than one calendar day (e.g. an export job that's slightly early/late);
  the loader filters to the requested report date and quarantines the rest
  as "wrong_day" rather than silently including them.
- Malformed rows (missing required field, bad date, non-numeric/negative
  quantity or price, unknown status) are skipped and counted as "bad rows",
  categorised, never crash the run.
- Rows that parse fine but fail a data-quality check are quarantined (kept
  out of `rows`, counted and listed separately so the exceptions report can
  show why they were dropped): wrong day, or a duplicate order_id already
  seen earlier in the same file.
- A missing file, an empty file, or a file present but with zero usable
  rows after filtering are all reported the same way: "no orders for the
  day" -- which is itself alert-worthy (see alert.py).
"""
import csv
import math
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from config import REQUIRED_COLUMNS, VALID_STATUSES

CAT_MISSING_FIELD = "missing_field"
CAT_BAD_DATE = "bad_date"
CAT_NON_NUMERIC_QTY = "non_numeric_quantity"
CAT_NON_NUMERIC_PRICE = "non_numeric_price"
CAT_NON_FINITE_PRICE = "non_finite_price"
CAT_UNKNOWN_STATUS = "unknown_status"

CAT_WRONG_DAY = "wrong_day"
CAT_DUPLICATE = "duplicate"

BAD_ROW_CATEGORIES = [
    CAT_MISSING_FIELD, CAT_BAD_DATE, CAT_NON_NUMERIC_QTY,
    CAT_NON_NUMERIC_PRICE, CAT_NON_FINITE_PRICE, CAT_UNKNOWN_STATUS,
]
QUARANTINE_CATEGORIES = [CAT_WRONG_DAY, CAT_DUPLICATE]


@dataclass
class OrderRow:
    order_id: str
    order_date: str
    customer_id: str
    sku: str
    quantity: int
    unit_price: float
    status: str
    product_name: str = ""
    category: str = ""
    region: str = ""
    currency: str = "USD"

    @property
    def line_total(self) -> float:
        return round(self.quantity * self.unit_price, 2)


@dataclass
class QuarantinedRow:
    category: str
    raw: dict
    detail: str


@dataclass
class LoadResult:
    rows: List[OrderRow] = field(default_factory=list)
    bad_row_categories: dict = field(default_factory=dict)  # category -> count
    quarantined: List[QuarantinedRow] = field(default_factory=list)
    file_present: bool = False
    file_empty: bool = False

    def total_bad_rows(self) -> int:
        return sum(self.bad_row_categories.values())


def _parse_date(raw: str) -> Optional[str]:
    raw = (raw or "").strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def load_orders(csv_path: str, report_date: str) -> LoadResult:
    result = LoadResult()

    if not os.path.isfile(csv_path):
        return result
    result.file_present = True

    seen_order_ids = set()

    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            result.file_empty = True
            return result

        any_row = False
        for raw in reader:
            any_row = True

            missing = [c for c in REQUIRED_COLUMNS if not (raw.get(c) or "").strip()]
            if missing:
                result.bad_row_categories[CAT_MISSING_FIELD] = (
                    result.bad_row_categories.get(CAT_MISSING_FIELD, 0) + 1
                )
                continue

            parsed_date = _parse_date(raw["order_date"])
            if parsed_date is None:
                result.bad_row_categories[CAT_BAD_DATE] = (
                    result.bad_row_categories.get(CAT_BAD_DATE, 0) + 1
                )
                continue

            try:
                quantity = int(raw["quantity"])
                if quantity <= 0:
                    raise ValueError("non-positive quantity")
            except (ValueError, TypeError):
                result.bad_row_categories[CAT_NON_NUMERIC_QTY] = (
                    result.bad_row_categories.get(CAT_NON_NUMERIC_QTY, 0) + 1
                )
                continue

            try:
                unit_price = float(raw["unit_price"])
            except (ValueError, TypeError):
                result.bad_row_categories[CAT_NON_NUMERIC_PRICE] = (
                    result.bad_row_categories.get(CAT_NON_NUMERIC_PRICE, 0) + 1
                )
                continue

            if not math.isfinite(unit_price) or unit_price < 0:
                result.bad_row_categories[CAT_NON_FINITE_PRICE] = (
                    result.bad_row_categories.get(CAT_NON_FINITE_PRICE, 0) + 1
                )
                continue

            status = (raw["status"] or "").strip().lower()
            if status not in VALID_STATUSES:
                result.bad_row_categories[CAT_UNKNOWN_STATUS] = (
                    result.bad_row_categories.get(CAT_UNKNOWN_STATUS, 0) + 1
                )
                continue

            order_id = raw["order_id"].strip()
            sku = raw["sku"].strip()

            if parsed_date != report_date:
                result.quarantined.append(QuarantinedRow(
                    category=CAT_WRONG_DAY, raw=dict(raw),
                    detail=f"order_date {parsed_date} != report date {report_date}",
                ))
                continue

            # Dedup key is the *line* (order_id + sku), not the order_id
            # alone: an order legitimately has one row per distinct SKU
            # line item, and order_id-only dedup was silently dropping
            # every SKU after the first on a multi-line order. Two rows
            # that share both order_id and sku are the real duplicate
            # case (e.g. an upstream export retry).
            line_key = (order_id, sku)
            if line_key in seen_order_ids:
                result.quarantined.append(QuarantinedRow(
                    category=CAT_DUPLICATE, raw=dict(raw),
                    detail=f"duplicate order line (order_id={order_id!r}, sku={sku!r}) (kept first occurrence)",
                ))
                continue
            seen_order_ids.add(line_key)

            result.rows.append(OrderRow(
                order_id=order_id,
                order_date=parsed_date,
                customer_id=raw["customer_id"].strip(),
                sku=raw["sku"].strip(),
                quantity=quantity,
                unit_price=round(unit_price, 2),
                status=status,
                product_name=(raw.get("product_name") or "").strip(),
                category=(raw.get("category") or "").strip(),
                region=(raw.get("region") or "").strip(),
                currency=(raw.get("currency") or "USD").strip() or "USD",
            ))

        if not any_row:
            result.file_empty = True

    return result
