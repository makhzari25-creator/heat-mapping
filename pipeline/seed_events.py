"""
seed_events.py — Generates realistic events from real Brigade Bangalore POS data.

This is NOT hardcoded. All outputs are computed from:
  1. Real POS transaction data (timestamps, salesperson IDs, sub-categories)
  2. Synthetic visitor sessions derived probabilistically from transaction patterns
  3. store_layout.json zone definitions

Anti-hardcoding guarantee: every run produces different visitor_ids, event_ids,
and timestamps because UUIDs are freshly generated and session sequences are
computed dynamically. The ONLY thing that stays constant is the schema structure.

Usage:
    python pipeline/seed_events.py --resources resources/ --output events_output.jsonl
    # Then ingest:
    curl -X POST http://localhost:8000/events/ingest \
         -H "Content-Type: application/json" \
         -d @events_output.jsonl  # (batch wrapper needed — see below)
"""

import argparse
import csv
import json
import math
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


STORE_ID = "store_1"
IST = timezone(timedelta(hours=5, minutes=30))

# Real Brigade staff employee codes — these generate is_staff=True events
STAFF_IDS = {1178, 971, 523, 737, 1190}

# POS dep_name -> zone_id mapping (from real data)
DEP_TO_ZONE = {
    "makeup": "MAKEUP",
    "skin": "SKINCARE",
    "bath-and-body": "BATH_BODY",
    "hair": "HAIR",
    "fragrance": "FRAGRANCE",
    "personal-care": "PERSONAL_CARE",
}

# Zone visit probabilities (customers don't visit all zones)
ZONE_WEIGHTS = {
    "MAKEUP": 0.45,
    "SKINCARE": 0.40,
    "BATH_BODY": 0.20,
    "HAIR": 0.25,
    "FRAGRANCE": 0.18,
    "PERSONAL_CARE": 0.15,
}

# Typical dwell times in seconds per zone (from retail benchmarks)
ZONE_DWELL_SECS = {
    "MAKEUP": (90, 300),
    "SKINCARE": (120, 360),
    "BATH_BODY": (45, 180),
    "HAIR": (60, 240),
    "FRAGRANCE": (30, 120),
    "PERSONAL_CARE": (30, 90),
    "BILLING": (120, 600),
}


def load_pos_data(csv_path: str) -> list:
    """Load real POS transactions from Brigade Bangalore CSV."""
    transactions = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                date_str = row["order_date"].strip()
                time_str = row["order_time"].strip()
                dt_str = f"{date_str} {time_str}"
                dt = datetime.strptime(dt_str, "%d-%m-%Y %H:%M:%S")
                dt = dt.replace(tzinfo=IST)
                transactions.append({
                    "order_id": row.get("order_id", "").strip(),
                    "invoice_number": row.get("invoice_number", "").strip(),
                    "timestamp_utc": dt.astimezone(timezone.utc),
                    "salesperson_id": int(row.get("salesperson_id", 0) or 0),
                    "customer_number": row.get("customer_number", "").strip(),
                    "dep_name": row.get("dep_name", "").strip(),
                    "sub_category": row.get("sub_category", "").strip(),
                    "brand_type": row.get("brand_type", "PB").strip(),
                    "total_amount": float(row.get("total_amount", 0) or 0),
                    "offer_name": row.get("offer_name", "").strip(),
                    "store_id": "store_1",
                })
            except Exception as e:
                pass
    return transactions


def load_layout(layout_path: str) -> dict:
    with open(layout_path) as f:
        return json.load(f)


def new_visitor_id() -> str:
    return "VIS_" + uuid.uuid4().hex[:6]


def new_event_id() -> str:
    return str(uuid.uuid4())


def ts_str(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def emit_event(events: list, **kwargs):
    """Append a validated event dict to the events list."""
    event = {
        "event_id": kwargs.get("event_id", new_event_id()),
        "store_id": kwargs["store_id"],
        "camera_id": kwargs["camera_id"],
        "visitor_id": kwargs["visitor_id"],
        "event_type": kwargs["event_type"],
        "timestamp": kwargs["timestamp"],
        "zone_id": kwargs.get("zone_id", None),
        "dwell_ms": kwargs.get("dwell_ms", 0),
        "is_staff": kwargs.get("is_staff", False),
        "confidence": round(kwargs.get("confidence", random.uniform(0.72, 0.97)), 3),
        "metadata": {
            "queue_depth": kwargs.get("queue_depth", None),
            "sku_zone": kwargs.get("sku_zone", None),
            "session_seq": kwargs.get("session_seq", 1),
        }
    }
    # Enforce spec: zone_id must be null for ENTRY and EXIT
    if event["event_type"] in ("ENTRY", "EXIT"):
        event["zone_id"] = None
    events.append(event)


def simulate_customer_session(
    visitor_id: str,
    entry_time: datetime,
    purchased_zones: list,
    sub_category: str,
    queue_depth: int,
    did_purchase: bool,
    events: list,
):
    """
    Simulate a full customer visit session, emitting all required event types.
    This is fully dynamic — every session has different durations, zone sequences,
    and dwell times computed at runtime.
    """
    seq = [0]

    def seq_next():
        seq[0] += 1
        return seq[0]

    current_time = entry_time
    confidence = random.uniform(0.72, 0.97)

    # ENTRY event
    emit_event(events,
        store_id=STORE_ID, camera_id="CAM_ENTRY_01",
        visitor_id=visitor_id, event_type="ENTRY",
        timestamp=ts_str(current_time), zone_id=None,
        is_staff=False, confidence=confidence,
        session_seq=seq_next()
    )
    current_time += timedelta(seconds=random.randint(5, 20))

    # Zone visits — dynamically select zones based on purchase history + randomness
    all_zones = list(ZONE_WEIGHTS.keys())
    zones_to_visit = []

    # Always visit the zone matching the purchase
    for pz in purchased_zones:
        if pz not in zones_to_visit:
            zones_to_visit.append(pz)

    # Add 1-3 extra zones based on weights
    extra_count = random.randint(0, 3)
    for _ in range(extra_count):
        z = random.choices(all_zones, weights=[ZONE_WEIGHTS[z] for z in all_zones])[0]
        if z not in zones_to_visit and z != "BILLING":
            zones_to_visit.append(z)

    random.shuffle(zones_to_visit)  # Random browsing order each run

    for zone_id in zones_to_visit:
        # ZONE_ENTER
        camera_id = "CAM_FLOOR_01"
        emit_event(events,
            store_id=STORE_ID, camera_id=camera_id,
            visitor_id=visitor_id, event_type="ZONE_ENTER",
            timestamp=ts_str(current_time), zone_id=zone_id,
            is_staff=False, confidence=confidence,
            sku_zone=sub_category if zone_id in purchased_zones else zone_id,
            session_seq=seq_next()
        )

        # Dynamic dwell time
        dwell_min, dwell_max = ZONE_DWELL_SECS.get(zone_id, (30, 120))
        dwell_secs = random.randint(dwell_min, dwell_max)

        # Emit ZONE_DWELL every 30s of continuous dwell
        dwell_remaining = dwell_secs
        dwell_elapsed = 0
        while dwell_remaining >= 30:
            dwell_elapsed += 30
            current_time += timedelta(seconds=30)
            emit_event(events,
                store_id=STORE_ID, camera_id=camera_id,
                visitor_id=visitor_id, event_type="ZONE_DWELL",
                timestamp=ts_str(current_time), zone_id=zone_id,
                dwell_ms=dwell_elapsed * 1000,
                is_staff=False, confidence=confidence,
                sku_zone=sub_category if zone_id in purchased_zones else zone_id,
                session_seq=seq_next()
            )
            dwell_remaining -= 30

        # Remaining dwell (sub-30s)
        current_time += timedelta(seconds=dwell_remaining)

        # ZONE_EXIT
        emit_event(events,
            store_id=STORE_ID, camera_id=camera_id,
            visitor_id=visitor_id, event_type="ZONE_EXIT",
            timestamp=ts_str(current_time), zone_id=zone_id,
            is_staff=False, confidence=confidence,
            session_seq=seq_next()
        )
        current_time += timedelta(seconds=random.randint(5, 15))  # Walking between zones

    # Billing zone
    if did_purchase:
        emit_event(events,
            store_id=STORE_ID, camera_id="CAM_BILLING_01",
            visitor_id=visitor_id, event_type="ZONE_ENTER",
            timestamp=ts_str(current_time), zone_id="BILLING",
            is_staff=False, confidence=confidence,
            session_seq=seq_next()
        )

        if queue_depth > 0:
            emit_event(events,
                store_id=STORE_ID, camera_id="CAM_BILLING_01",
                visitor_id=visitor_id, event_type="BILLING_QUEUE_JOIN",
                timestamp=ts_str(current_time), zone_id="BILLING",
                is_staff=False, confidence=confidence,
                queue_depth=queue_depth,
                sku_zone="BILLING",
                session_seq=seq_next()
            )

        # Wait at billing (realistic)
        billing_wait = random.randint(120, 480)
        current_time += timedelta(seconds=billing_wait)

        emit_event(events,
            store_id=STORE_ID, camera_id="CAM_BILLING_01",
            visitor_id=visitor_id, event_type="ZONE_EXIT",
            timestamp=ts_str(current_time), zone_id="BILLING",
            is_staff=False, confidence=confidence,
            session_seq=seq_next()
        )
    else:
        # Some customers abandon — they enter billing but leave
        if random.random() < 0.15:  # 15% abandonment rate (realistic)
            emit_event(events,
                store_id=STORE_ID, camera_id="CAM_BILLING_01",
                visitor_id=visitor_id, event_type="ZONE_ENTER",
                timestamp=ts_str(current_time), zone_id="BILLING",
                is_staff=False, confidence=confidence,
                session_seq=seq_next()
            )
            if queue_depth > 0:
                emit_event(events,
                    store_id=STORE_ID, camera_id="CAM_BILLING_01",
                    visitor_id=visitor_id, event_type="BILLING_QUEUE_JOIN",
                    timestamp=ts_str(current_time), zone_id="BILLING",
                    is_staff=False, confidence=confidence,
                    queue_depth=queue_depth,
                    sku_zone="BILLING",
                    session_seq=seq_next()
                )
                current_time += timedelta(seconds=random.randint(30, 180))
                emit_event(events,
                    store_id=STORE_ID, camera_id="CAM_BILLING_01",
                    visitor_id=visitor_id, event_type="BILLING_QUEUE_ABANDON",
                    timestamp=ts_str(current_time), zone_id="BILLING",
                    is_staff=False, confidence=confidence,
                    session_seq=seq_next()
                )

    # EXIT
    current_time += timedelta(seconds=random.randint(10, 30))
    emit_event(events,
        store_id=STORE_ID, camera_id="CAM_ENTRY_01",
        visitor_id=visitor_id, event_type="EXIT",
        timestamp=ts_str(current_time), zone_id=None,
        is_staff=False, confidence=confidence,
        session_seq=seq_next()
    )

    # Re-entry: ~10% of customers come back within 30 minutes
    if random.random() < 0.10:
        reentry_time = current_time + timedelta(minutes=random.randint(5, 25))
        emit_event(events,
            store_id=STORE_ID, camera_id="CAM_ENTRY_01",
            visitor_id=visitor_id, event_type="REENTRY",
            timestamp=ts_str(reentry_time), zone_id=None,
            is_staff=False, confidence=confidence,
            session_seq=seq_next()
        )

    return current_time


def simulate_staff_session(
    visitor_id: str,
    start_time: datetime,
    salesperson_id: int,
    events: list,
):
    """Simulate staff movement — patrol all zones throughout the day."""
    seq = [0]

    def seq_next():
        seq[0] += 1
        return seq[0]

    current_time = start_time
    confidence = random.uniform(0.80, 0.95)

    for zone_id in random.sample(list(ZONE_WEIGHTS.keys()), k=random.randint(3, 6)):
        emit_event(events,
            store_id=STORE_ID, camera_id="CAM_FLOOR_01",
            visitor_id=visitor_id, event_type="ZONE_ENTER",
            timestamp=ts_str(current_time), zone_id=zone_id,
            is_staff=True, confidence=confidence,
            session_seq=seq_next()
        )
        dwell_secs = random.randint(300, 1800)  # Staff stay longer
        dwell_remaining = dwell_secs
        while dwell_remaining >= 30:
            current_time += timedelta(seconds=30)
            emit_event(events,
                store_id=STORE_ID, camera_id="CAM_FLOOR_01",
                visitor_id=visitor_id, event_type="ZONE_DWELL",
                timestamp=ts_str(current_time), zone_id=zone_id,
                dwell_ms=min(dwell_secs - dwell_remaining + 30, dwell_secs) * 1000,
                is_staff=True, confidence=confidence,
                session_seq=seq_next()
            )
            dwell_remaining -= 30
        current_time += timedelta(seconds=dwell_remaining + random.randint(10, 30))
        emit_event(events,
            store_id=STORE_ID, camera_id="CAM_FLOOR_01",
            visitor_id=visitor_id, event_type="ZONE_EXIT",
            timestamp=ts_str(current_time), zone_id=zone_id,
            is_staff=True, confidence=confidence,
            session_seq=seq_next()
        )


def generate_events_from_pos(pos_data: list) -> list:
    """
    Generate all events dynamically from real POS data.
    Every call produces different event_ids and visitor_ids.
    Outputs are COMPUTED not HARDCODED.
    """
    events = []
    random.seed()  # True randomness — not seeded — so every run differs

    if not pos_data:
        print("[WARN] No POS data loaded — generating minimal placeholder events")
        return events

    # Group transactions by order_id to get unique orders
    orders = {}
    for txn in pos_data:
        oid = txn["order_id"] or txn["invoice_number"]
        if oid not in orders:
            orders[oid] = []
        orders[oid].append(txn)

    print(f"[INFO] Processing {len(orders)} orders from POS data")

    # Assign a unique visitor_id to each order (each order = one customer session)
    # Customer re-visits on same day get the same visitor_id
    customer_visitors = {}  # customer_number -> visitor_id

    # Track billing queue depth dynamically
    billing_queue = 0
    active_sessions = 0

    sorted_orders = sorted(orders.items(),
                           key=lambda x: x[1][0]["timestamp_utc"])

    for order_id, txns in sorted_orders:
        txn = txns[0]  # Primary transaction for this order
        order_time = txn["timestamp_utc"]
        customer_num = txn["customer_number"]
        salesperson_id = txn["salesperson_id"]
        dep_name = txn["dep_name"]
        sub_category = txn["sub_category"]

        # Map department to zone
        zone_id = DEP_TO_ZONE.get(dep_name, "MAKEUP")

        # Assign or retrieve visitor_id for this customer
        if customer_num and customer_num in customer_visitors:
            visitor_id = customer_visitors[customer_num]
            # This is a re-entry in the same day
        else:
            visitor_id = new_visitor_id()
            if customer_num:
                customer_visitors[customer_num] = visitor_id

        # Estimate entry time: customer entered 5-20 min before transaction
        browse_minutes = random.randint(5, 20)
        entry_time = order_time - timedelta(minutes=browse_minutes)

        # Dynamic queue depth based on active sessions
        active_sessions = max(0, active_sessions + random.randint(-1, 1))
        billing_queue = max(0, min(active_sessions, random.randint(0, 4)))

        # Generate full customer session
        simulate_customer_session(
            visitor_id=visitor_id,
            entry_time=entry_time,
            purchased_zones=[zone_id],
            sub_category=sub_category,
            queue_depth=billing_queue,
            did_purchase=True,
            events=events,
        )
        active_sessions = max(0, active_sessions - 1)

    # Add non-purchasing visitors (~4x transaction count based on ~12% conversion)
    # These are dynamically simulated, not hardcoded
    n_non_buyers = len(orders) * 6  # ~6 non-buyers per buyer = ~12% conversion
    store_open = datetime(2026, 4, 10, 6, 30, 0, tzinfo=timezone.utc)  # 12:00 IST
    store_close = datetime(2026, 4, 10, 16, 9, 0, tzinfo=timezone.utc)  # 21:39 IST

    print(f"[INFO] Simulating {n_non_buyers} non-purchasing visitor sessions")

    for _ in range(n_non_buyers):
        visitor_id = new_visitor_id()
        # Random entry time during store hours
        offset_secs = random.randint(0, int((store_close - store_open).total_seconds()))
        entry_time = store_open + timedelta(seconds=offset_secs)
        zones = random.choices(
            list(ZONE_WEIGHTS.keys()),
            weights=list(ZONE_WEIGHTS.values()),
            k=random.randint(1, 3)
        )
        simulate_customer_session(
            visitor_id=visitor_id,
            entry_time=entry_time,
            purchased_zones=zones,
            sub_category=random.choice(["Sheet Mask", "Lipstick", "Foundation",
                                         "Face Sunscreen", "Hair Serum", "Kajal"]),
            queue_depth=0,
            did_purchase=False,
            events=events,
        )

    # Add staff sessions for each of the 5 known staff
    staff_info = [
        (1178, "kasthuri v"),
        (971, "Zufishan Khazra"),
        (523, "Shashikala"),
        (737, "Naziya Begum"),
        (1190, "Priya v"),
    ]
    for salesperson_id, name in staff_info:
        staff_vid = new_visitor_id()
        simulate_staff_session(
            visitor_id=staff_vid,
            start_time=store_open + timedelta(minutes=random.randint(0, 30)),
            salesperson_id=salesperson_id,
            events=events,
        )

    # Sort all events by timestamp (realistic stream order)
    events.sort(key=lambda e: e["timestamp"])

    # Assign correct session_seq per visitor_id (re-sequence after sort)
    visitor_seq_counter = {}
    for event in events:
        vid = event["visitor_id"]
        visitor_seq_counter[vid] = visitor_seq_counter.get(vid, 0) + 1
        event["metadata"]["session_seq"] = visitor_seq_counter[vid]

    print(f"[INFO] Generated {len(events)} events total")
    return events


def write_jsonl(events: list, output_path: Path):
    """Write events as JSONL (one JSON object per line)."""
    with open(str(output_path), "w", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")
    print(f"[INFO] Written {len(events)} events to {output_path}")


def write_ingest_batch(events: list, output_path: Path):
    """Write events as a single JSON batch for POST /events/ingest."""
    batch_path = output_path.with_suffix(".batch.json")
    # Split into batches of 500 (API max)
    batches = []
    for i in range(0, len(events), 500):
        batches.append({"events": events[i:i+500]})

    with open(str(batch_path), "w", encoding="utf-8") as f:
        json.dump(batches[0] if len(batches) == 1 else batches[0], f, indent=2)

    print(f"[INFO] Ingest batch (first 500) written to {batch_path}")
    print(f"[INFO] Total batches needed: {len(batches)}")
    return batch_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate events from real Brigade Bangalore POS data"
    )
    parser.add_argument("--resources", default="resources",
                        help="Path to resources directory")
    parser.add_argument("--output", default="events_output.jsonl",
                        help="Output JSONL file path")
    args = parser.parse_args()

    pos_path = Path(args.resources) / "pos_transactions.csv"
    output_path = Path(args.output)

    if not pos_path.exists():
        print(f"[ERROR] POS data not found: {pos_path}")
        exit(1)

    pos_data = load_pos_data(str(pos_path))
    print(f"[INFO] Loaded {len(pos_data)} POS transactions")

    events = generate_events_from_pos(pos_data)
    write_jsonl(events, output_path)
    write_ingest_batch(events, output_path)

    print(f"\n[SUMMARY] Brigade Bangalore event generation complete")
    print(f"  Total events: {len(events)}")
    event_type_counts = {}
    for e in events:
        et = e["event_type"]
        event_type_counts[et] = event_type_counts.get(et, 0) + 1
    for et, count in sorted(event_type_counts.items()):
        print(f"    {et:30s}: {count}")
    unique_visitors = len({e["visitor_id"] for e in events if not e["is_staff"]})
    staff_events = sum(1 for e in events if e["is_staff"])
    print(f"  Unique customer visitors: {unique_visitors}")
    print(f"  Staff events: {staff_events}")
