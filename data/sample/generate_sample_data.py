#!/usr/bin/env python3
"""
Generates fully synthetic orders.csv for the demo -- fixed seed, no real
customers, products, or companies. Deliberately plants a few rows that
exercise every validation path (missing field, bad date, non-numeric
quantity, negative price, unknown status, wrong-day row, duplicate
order_id, price outlier) so the sample report/exceptions output is
representative, not just a clean happy path.

Usage: python3 generate_sample_data.py [--out orders.csv] [--date YYYY-MM-DD] [--seed 42]
"""
import argparse
import csv
import random

PRODUCTS = [
    ("SKU-1001", "Aurora Desk Lamp", "Home"),
    ("SKU-1002", "Nimbus Backpack", "Bags"),
    ("SKU-1003", "Cobalt Water Bottle", "Outdoor"),
    ("SKU-1004", "Quartz Wireless Mouse", "Electronics"),
    ("SKU-1005", "Fernwood Notebook Set", "Stationery"),
    ("SKU-1006", "Drift Bluetooth Speaker", "Electronics"),
    ("SKU-1007", "Basalt Coffee Grinder", "Kitchen"),
    ("SKU-1008", "Meridian Yoga Mat", "Fitness"),
    ("SKU-1009", "Solace Reading Pillow", "Home"),
    ("SKU-1010", "Pathway Trail Shoes", "Outdoor"),
]
REGIONS = ["north-america", "europe", "apac", "latam"]
STATUSES = ["completed", "completed", "completed", "completed", "refunded", "cancelled"]


def gen(out_path: str, report_date: str, seed: int, n_orders: int = 220):
    rnd = random.Random(seed)
    rows = []
    order_seq = 1

    def next_order_id():
        nonlocal order_seq
        oid = f"ORD-{report_date.replace('-', '')}-{order_seq:04d}"
        order_seq += 1
        return oid

    for _ in range(n_orders):
        sku, name, category = rnd.choice(PRODUCTS)
        rows.append({
            "order_id": next_order_id(),
            "order_date": report_date,
            "customer_id": f"CUST-{rnd.randint(1000, 9999)}",
            "sku": sku,
            "product_name": name,
            "category": category,
            "quantity": rnd.choice([1, 1, 1, 2, 2, 3, 5]),
            "unit_price": round(rnd.uniform(8.0, 120.0), 2),
            "status": rnd.choice(STATUSES),
            "region": rnd.choice(REGIONS),
            "currency": "USD",
        })

    # A couple of genuine high-value orders (bulk purchase) so revenue
    # concentration has something real to show.
    rows.append({
        "order_id": next_order_id(), "order_date": report_date,
        "customer_id": "CUST-5551", "sku": "SKU-1006", "product_name": "Drift Bluetooth Speaker",
        "category": "Electronics", "quantity": 40, "unit_price": 59.0,
        "status": "completed", "region": "europe", "currency": "USD",
    })

    # --- deliberately planted exception rows -----------------------------
    rows.append({  # missing customer_id
        "order_id": next_order_id(), "order_date": report_date, "customer_id": "",
        "sku": "SKU-1002", "product_name": "Nimbus Backpack", "category": "Bags",
        "quantity": 1, "unit_price": 64.0, "status": "completed",
        "region": "apac", "currency": "USD",
    })
    rows.append({  # bad date
        "order_id": next_order_id(), "order_date": "not-a-date", "customer_id": "CUST-1200",
        "sku": "SKU-1004", "product_name": "Quartz Wireless Mouse", "category": "Electronics",
        "quantity": 1, "unit_price": 24.5, "status": "completed",
        "region": "north-america", "currency": "USD",
    })
    rows.append({  # non-numeric quantity
        "order_id": next_order_id(), "order_date": report_date, "customer_id": "CUST-1201",
        "sku": "SKU-1003", "product_name": "Cobalt Water Bottle", "category": "Outdoor",
        "quantity": "many", "unit_price": 18.0, "status": "completed",
        "region": "latam", "currency": "USD",
    })
    rows.append({  # negative price
        "order_id": next_order_id(), "order_date": report_date, "customer_id": "CUST-1202",
        "sku": "SKU-1007", "product_name": "Basalt Coffee Grinder", "category": "Kitchen",
        "quantity": 1, "unit_price": -30.0, "status": "completed",
        "region": "europe", "currency": "USD",
    })
    rows.append({  # unknown status
        "order_id": next_order_id(), "order_date": report_date, "customer_id": "CUST-1203",
        "sku": "SKU-1009", "product_name": "Solace Reading Pillow", "category": "Home",
        "quantity": 1, "unit_price": 42.0, "status": "backordered",
        "region": "apac", "currency": "USD",
    })
    prev_day = "2026-01-14" if report_date == "2026-01-15" else "2026-01-14"
    rows.append({  # wrong-day row (belongs to yesterday's export)
        "order_id": next_order_id(), "order_date": prev_day, "customer_id": "CUST-1204",
        "sku": "SKU-1010", "product_name": "Pathway Trail Shoes", "category": "Outdoor",
        "quantity": 1, "unit_price": 75.0, "status": "completed",
        "region": "north-america", "currency": "USD",
    })
    dup_id = next_order_id()
    dup_row = {
        "order_id": dup_id, "order_date": report_date, "customer_id": "CUST-1205",
        "sku": "SKU-1005", "product_name": "Fernwood Notebook Set", "category": "Stationery",
        "quantity": 2, "unit_price": 15.0, "status": "completed",
        "region": "europe", "currency": "USD",
    }
    rows.append(dup_row)
    rows.append(dict(dup_row))  # exact duplicate order_id -- should be quarantined

    rnd.shuffle(rows)

    fieldnames = [
        "order_id", "order_date", "customer_id", "sku", "product_name",
        "category", "quantity", "unit_price", "status", "region", "currency",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    print(f"wrote {len(rows)} rows to {out_path} (report date {report_date}, seed {seed})")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="orders.csv")
    p.add_argument("--date", default="2026-01-15")
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()
    gen(args.out, args.date, args.seed)
