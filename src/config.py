"""
Configuration for the daily sales report tool.

Every knob a client is likely to want to change lives here, overridable via
environment variables (useful for cron/systemd without editing this file).
"""
import os

REQUIRED_COLUMNS = [
    "order_id", "order_date", "customer_id", "sku",
    "quantity", "unit_price", "status",
]
OPTIONAL_COLUMNS = ["product_name", "category", "region", "currency"]

VALID_STATUSES = {"completed", "refunded", "cancelled"}

# A line total (quantity * unit_price) at or above this is flagged as a
# statistical outlier in the exceptions report (still counted in revenue --
# outliers are a review flag, not a rejection).
OUTLIER_LINE_TOTAL_STDEV = float(os.environ.get("SALES_OUTLIER_STDEV", "3.0"))
# Absolute floor below which the stdev rule never fires (avoids flagging
# everything on a tiny/quiet sample day where the stdev itself is small).
OUTLIER_MIN_LINE_TOTAL = float(os.environ.get("SALES_OUTLIER_MIN_TOTAL", "500.0"))

# Revenue share (0-1) a single SKU can command before it's called out in the
# report as a concentration flag (informational, not an error).
TOP_SKU_CONCENTRATION_ALERT = float(os.environ.get("SALES_CONCENTRATION_ALERT", "0.40"))


def get_state_dir():
    """Where per-day dedup/idempotency state lives (state/<date>.json).
    Overridable via env SALES_STATE_DIR. Default: state/ next to the repo
    root (sibling of src/, data/, output/)."""
    default = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state"
    )
    return os.environ.get("SALES_STATE_DIR", default)
