"""
startup_seed.py — Seeds the database with events derived from POS data for both stores.
"""

import csv
import json
import os
import random
import uuid
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from typing import List, Optional

DEP_TO_ZONE = {
    "makeup": "MAKEUP",
    "skin": "SKINCARE",
    "bath-and-body": "BATH_BODY",
    "hair": "HAIR",
    "fragrance": "FRAGRANCE",
    "personal-care": "PERSONAL_CARE",
}

ZONE_WEIGHTS = {
    "MAKEUP": 0.45,
    "SKINCARE": 0.40,
    "BATH_BODY": 0.20,
    "HAIR": 0.25,
    "FRAGRANCE": 0.18,
    "PERSONAL_CARE": 0.15,
}

ZONE_DWELL_SECS = {
    "MAKEUP": (90, 300),
    "SKINCARE": (120, 360),
    "BATH_BODY": (45, 180),
    "HAIR": (60, 240),
    "FRAGRANCE": (30, 120),
    "PERSONAL_CARE": (30, 90),
    "BILLING": (120, 600),
}

def _new_vid() -> str:
    return "VIS_" + uuid.uuid4().hex[:6]

def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _get_cams(store_id: str):
    suffix = "_01" if store_id == "store_1" else "_02"
    return {
        "entry": f"CAM_ENTRY{suffix}",
        "floor": f"CAM_FLOOR{suffix}",
        "billing": f"CAM_BILLING{suffix}"
    }

def _mk(store_id, camera_id, visitor_id, event_type, timestamp,
        zone_id=None, dwell_ms=0, is_staff=False, confidence=0.85,
        queue_depth=None, sku_zone=None, session_seq=1,
        amount=None, salesperson_id=None) -> dict:
    if event_type in ("ENTRY", "EXIT", "REENTRY"):
        zone_id = None
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": store_id,
        "camera_id": camera_id,
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": timestamp,
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": is_staff,
        "confidence": round(confidence, 3),
        "metadata": {
            "queue_depth": queue_depth,
            "sku_zone": sku_zone,
            "session_seq": session_seq,
            "amount": amount,
            "salesperson_id": salesperson_id
        }
    }

def _simulate_session(store_id, visitor_id, entry_time, zones, sub_cat,
                       queue_depth, did_purchase, is_staff=False,
                       amount=None, salesperson_id=None):
    events = []
    t = entry_time
    conf = random.uniform(0.70, 0.97)
    seq = 0
    cams = _get_cams(store_id)

    def s():
        nonlocal seq
        seq += 1
        return seq

    cam = cams["floor"] if is_staff else cams["entry"]
    if not is_staff:
        events.append(_mk(store_id, cams["entry"], visitor_id, "ENTRY",
                          _ts(t), confidence=conf, session_seq=s()))
        t += timedelta(seconds=random.randint(5, 20))

    for zone_id in zones:
        events.append(_mk(store_id, cams["floor"], visitor_id, "ZONE_ENTER",
                          _ts(t), zone_id=zone_id, confidence=conf,
                          sku_zone=sub_cat if not is_staff else None,
                          session_seq=s()))
        dmin, dmax = ZONE_DWELL_SECS.get(zone_id, (30, 90))
        dwell_secs = random.randint(dmin, dmax)
        elapsed = 0
        while dwell_secs - elapsed >= 30:
            t += timedelta(seconds=30)
            elapsed += 30
            events.append(_mk(store_id, cams["floor"], visitor_id, "ZONE_DWELL",
                              _ts(t), zone_id=zone_id, dwell_ms=elapsed * 1000,
                              is_staff=is_staff, confidence=conf,
                              sku_zone=sub_cat, session_seq=s()))
        t += timedelta(seconds=max(0, dwell_secs - elapsed))
        events.append(_mk(store_id, cams["floor"], visitor_id, "ZONE_EXIT",
                          _ts(t), zone_id=zone_id, is_staff=is_staff,
                          confidence=conf, session_seq=s()))
        t += timedelta(seconds=random.randint(5, 15))

    if not is_staff:
        if did_purchase:
            events.append(_mk(store_id, cams["billing"], visitor_id, "ZONE_ENTER",
                              _ts(t), zone_id="BILLING", confidence=conf, session_seq=s()))
            if queue_depth > 0:
                events.append(_mk(store_id, cams["billing"], visitor_id,
                                  "BILLING_QUEUE_JOIN", _ts(t), zone_id="BILLING",
                                  confidence=conf, queue_depth=queue_depth,
                                  sku_zone="BILLING", session_seq=s()))
            t += timedelta(seconds=random.randint(120, 480))
            events.append(_mk(store_id, cams["billing"], visitor_id, "ZONE_EXIT",
                              _ts(t), zone_id="BILLING", confidence=conf, session_seq=s(),
                              amount=amount, salesperson_id=salesperson_id))
        elif random.random() < 0.15:
            events.append(_mk(store_id, cams["billing"], visitor_id, "ZONE_ENTER",
                              _ts(t), zone_id="BILLING", confidence=conf, session_seq=s()))
            if queue_depth > 0:
                events.append(_mk(store_id, cams["billing"], visitor_id,
                                  "BILLING_QUEUE_JOIN", _ts(t), zone_id="BILLING",
                                  confidence=conf, queue_depth=queue_depth,
                                  sku_zone="BILLING", session_seq=s()))
                t += timedelta(seconds=random.randint(30, 180))
                events.append(_mk(store_id, cams["billing"], visitor_id,
                                  "BILLING_QUEUE_ABANDON", _ts(t), zone_id="BILLING",
                                  confidence=conf, session_seq=s()))

        t += timedelta(seconds=random.randint(10, 30))
        events.append(_mk(store_id, cams["entry"], visitor_id, "EXIT",
                          _ts(t), confidence=conf, session_seq=s()))

        if random.random() < 0.10:
            t2 = t + timedelta(minutes=random.randint(5, 25))
            events.append(_mk(store_id, cams["entry"], visitor_id, "REENTRY",
                              _ts(t2), confidence=conf, session_seq=s()))

    return events

def generate_seed_events(target_date=None, store_id="store_1") -> List[dict]:
    """Generate dynamic seed events for the specified store."""
    random.seed()
    if target_date is None:
        target_date = date(2026, 4, 10)
    elif isinstance(target_date, str):
        try:
            target_date = date.fromisoformat(target_date)
        except Exception:
            target_date = date(2026, 4, 10)

    # Try multiple candidate paths for Docker and local dev
    candidates = [
        Path("resources/pos_transactions.csv"),
        Path("/app/resources/pos_transactions.csv"),
        Path(__file__).parent.parent / "resources" / "pos_transactions.csv",
    ]
    pos_path = next((p for p in candidates if p.exists()), None)

    if pos_path is None:
        return []

    # Load POS data
    transactions = []
    with open(str(pos_path), newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                # Parse transaction timestamp (assuming India Standard Time format)
                from datetime import timezone as tz, timedelta as td
                ist_tz = tz(td(hours=5, minutes=30))
                dt = datetime.strptime(
                    f"{row['order_date'].strip()} {row['order_time'].strip()}",
                    "%d-%m-%Y %H:%M:%S"
                ).replace(tzinfo=ist_tz)
                
                amount = float(row.get("total_amount", 0) or 0)
                salesperson_id = int(row.get("salesperson_id", 0) or 0)
                
                # Apply store specific offsets to separate dataset values
                if store_id == "store_2":
                    amount = round(amount * 1.15, 2)
                    salesperson_id = (salesperson_id % 5) + 2001
                
                transactions.append({
                    "order_id": row.get("order_id", row.get("invoice_number", "")).strip(),
                    "timestamp_utc": dt.astimezone(timezone.utc),
                    "customer_number": row.get("customer_number", "").strip(),
                    "salesperson_id": salesperson_id,
                    "dep_name": row.get("dep_name", "makeup").strip(),
                    "sub_category": row.get("sub_category", "").strip(),
                    "total_amount": amount,
                })
            except Exception:
                pass

    all_events = []
    customer_visitors = {}
    seen_orders = set()

    store_open = datetime(target_date.year, target_date.month, target_date.day, 6, 30, 0, tzinfo=timezone.utc)
    store_close = datetime(target_date.year, target_date.month, target_date.day, 16, 9, 0, tzinfo=timezone.utc)

    # One session per unique order
    for txn in sorted(transactions, key=lambda x: x["timestamp_utc"]):
        oid = txn["order_id"]
        if oid in seen_orders:
            continue
        seen_orders.add(oid)

        cnum = txn["customer_number"]
        if cnum and cnum in customer_visitors:
            vid = customer_visitors[cnum]
        else:
            vid = _new_vid()
            if cnum:
                customer_visitors[cnum] = vid

        zone = DEP_TO_ZONE.get(txn["dep_name"], "MAKEUP")
        
        # Shift the POS timestamp to target_date
        txn_t = txn["timestamp_utc"]
        shifted_txn_t = datetime(target_date.year, target_date.month, target_date.day,
                                 txn_t.hour, txn_t.minute, txn_t.second, tzinfo=timezone.utc)
        entry_time = shifted_txn_t - timedelta(minutes=random.randint(5, 20))
        
        zones = [zone] + random.choices(
            list(ZONE_WEIGHTS.keys()),
            weights=list(ZONE_WEIGHTS.values()),
            k=random.randint(0, 2)
        )
        zones = list(dict.fromkeys(zones))  # de-dup, preserve order

        all_events.extend(_simulate_session(
            store_id, vid, entry_time, zones, txn["sub_category"],
            queue_depth=random.randint(0, 3),
            did_purchase=True,
            amount=txn["total_amount"],
            salesperson_id=txn["salesperson_id"]
        ))

    # Non-purchasing visitors (~6x orders)
    n_nonbuyers = len(seen_orders) * 6
    for _ in range(n_nonbuyers):
        vid = _new_vid()
        offset = random.randint(0, int((store_close - store_open).total_seconds()))
        entry_t = store_open + timedelta(seconds=offset)
        zones = random.choices(
            list(ZONE_WEIGHTS.keys()),
            weights=list(ZONE_WEIGHTS.values()),
            k=random.randint(1, 3)
        )
        sub = random.choice(["Sheet Mask", "Lipstick", "Foundation",
                              "Face Sunscreen", "Hair Serum", "Kajal", "Eyeliner"])
        all_events.extend(_simulate_session(
            store_id, vid, entry_t, list(dict.fromkeys(zones)), sub,
            queue_depth=0, did_purchase=False
        ))

    # Staff sessions
    if store_id == "store_2":
        staff = [(2001, "Aarav Sharma"), (2002, "Ishita Patel"),
                 (2003, "Rohan Deshmukh"), (2004, "Ananya Sen"), (2005, "Kabir Sen")]
    else:
        staff = [(1178, "kasthuri v"), (971, "Zufishan Khazra"),
                 (523, "Shashikala v"), (737, "Naziya Begum"), (1190, "Priya v")]

    for sid, name in staff:
        svid = _new_vid()
        st = store_open + timedelta(minutes=random.randint(0, 30))
        zones = random.sample(list(ZONE_WEIGHTS.keys()), k=random.randint(3, 6))
        all_events.extend(_simulate_session(
            store_id, svid, st, zones, None, queue_depth=0,
            did_purchase=False, is_staff=True
        ))

    all_events.sort(key=lambda e: e["timestamp"])

    # Re-sequence session_seq per visitor
    vsq = {}
    for e in all_events:
        vid = e["visitor_id"]
        vsq[vid] = vsq.get(vid, 0) + 1
        e["metadata"]["session_seq"] = vsq[vid]

    return all_events
