"""
Anomaly detection — 3 mandatory types:
  1. QUEUE_SPIKE — billing queue exceeds threshold
  2. CONVERSION_DROP — today's rate vs 7-day average
  3. DEAD_ZONE — no visits to a zone in 30+ minutes

Brigade Bangalore context for suggested_action strings:
- Known active promotions: Buy 2 Get 1 on PB, Sheet Mask 2+2, etc.
- 6 product zones: MAKEUP, SKINCARE, BATH_BODY, HAIR, FRAGRANCE, PERSONAL_CARE
- 5 salesperson team: kasthuri v, Zufishan Khazra, Shashikala, Naziya Begum, Priya v
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from database import get_db
from models import AnomaliesResponse, AnomalyItem, AnomalySeverity
from datetime import datetime, timedelta, date
import uuid
import json
from utils.cache import cache

router = APIRouter()

QUEUE_WARN = 5
QUEUE_CRITICAL = 10
CONVERSION_DROP_WARN_PCT = 20
CONVERSION_DROP_CRITICAL_PCT = 40
DEAD_ZONE_MINUTES = 30

ZONE_NAMES = {
    "MAKEUP": "Makeup & Colour",
    "SKINCARE": "Skincare & Treatments",
    "BATH_BODY": "Bath & Body",
    "HAIR": "Hair Care",
    "FRAGRANCE": "Fragrance",
    "PERSONAL_CARE": "Personal Care",
}


@router.get("/stores/{store_id}/anomalies", response_model=AnomaliesResponse)
async def get_anomalies(
    store_id: str,
    date_str: str = None,
    db: AsyncSession = Depends(get_db)
):
    is_today = date_str is None or date_str == date.today().isoformat()
    cache_key = f"anomalies_today:{store_id}"
    
    if is_today:
        try:
            cached = await cache.get(cache_key)
            if cached:
                return AnomaliesResponse(**cached)
        except Exception:
            pass

    anomalies = []
    now = datetime.utcnow()
    today = date_str or date.today().isoformat()

    # ── 1. Queue Spike ────────────────────────────────────────────────
    result = await db.execute(text("""
        SELECT metadata FROM events
        WHERE store_id=:sid AND event_type='BILLING_QUEUE_JOIN'
        ORDER BY timestamp DESC LIMIT 1
    """), {"sid": store_id})
    row = result.fetchone()
    if row:
        try:
            meta = json.loads(row[0])
            queue_depth = meta.get("queue_depth") or 0
        except Exception:
            queue_depth = 0
        if queue_depth >= QUEUE_WARN:
            sev = (
                AnomalySeverity.CRITICAL
                if queue_depth >= QUEUE_CRITICAL
                else AnomalySeverity.WARN
            )
            anomalies.append(AnomalyItem(
                anomaly_id=str(uuid.uuid4()),
                anomaly_type="QUEUE_SPIKE",
                severity=sev,
                description=(
                    f"Billing queue depth is {queue_depth} — "
                    f"exceeds threshold of {QUEUE_WARN}"
                ),
                suggested_action=(
                    "Open additional billing counter immediately. "
                    "Assign Zufishan Khazra or Priya v to billing — they are the "
                    "highest-volume salespersons. Priority-fast-track customers "
                    "with large baskets. Consider activating the self-checkout mode "
                    "for single-item purchases."
                ),
                detected_at=now.isoformat() + "Z",
                zone_id="BILLING",
                metric_value=float(queue_depth),
                threshold_value=float(QUEUE_WARN)
            ))

    # ── 2. Conversion Drop vs 7-day average ─────────────────────────
    result = await db.execute(text("""
        SELECT
            substr(timestamp, 1, 10) AS dt,
            COUNT(DISTINCT CASE WHEN event_type='ENTRY' AND is_staff=0 THEN visitor_id END),
            COUNT(DISTINCT CASE WHEN event_type='BILLING_QUEUE_JOIN' AND is_staff=0 THEN visitor_id END)
        FROM events
        WHERE store_id=:sid
          AND timestamp >= date(:today, '-8 days')
          AND timestamp < :today
        GROUP BY dt
        ORDER BY dt DESC LIMIT 7
    """), {"sid": store_id, "today": today})
    historical = result.fetchall()

    if historical:
        hist_rates = [
            (r[2] / r[1]) if r[1] > 0 else 0.0
            for r in historical
        ]
        avg_7day = sum(hist_rates) / len(hist_rates)

        t_start = f"{today}T00:00:00"
        t_end = f"{today}T23:59:59.999Z"
        result = await db.execute(text("""
            SELECT
                COUNT(DISTINCT CASE WHEN event_type='ENTRY' AND is_staff=0 THEN visitor_id END),
                COUNT(DISTINCT CASE WHEN event_type='BILLING_QUEUE_JOIN' AND is_staff=0 THEN visitor_id END)
            FROM events WHERE store_id=:sid AND timestamp >= :t_start AND timestamp <= :t_end
        """), {"sid": store_id, "t_start": t_start, "t_end": t_end})
        today_row = result.fetchone()
        today_rate = (
            (today_row[1] / today_row[0])
            if today_row and today_row[0] and today_row[0] > 0
            else 0.0
        )

        if avg_7day > 0:
            drop_pct = ((avg_7day - today_rate) / avg_7day) * 100
            if drop_pct >= CONVERSION_DROP_WARN_PCT:
                sev = (
                    AnomalySeverity.CRITICAL
                    if drop_pct >= CONVERSION_DROP_CRITICAL_PCT
                    else AnomalySeverity.WARN
                )
                anomalies.append(AnomalyItem(
                    anomaly_id=str(uuid.uuid4()),
                    anomaly_type="CONVERSION_DROP",
                    severity=sev,
                    description=(
                        f"Today's conversion rate ({today_rate:.1%}) is "
                        f"{drop_pct:.0f}% below 7-day average ({avg_7day:.1%}) "
                        f"for Brigade Bangalore"
                    ),
                    suggested_action=(
                        "Review zone traffic — check MAKEUP and SKINCARE zones (top revenue "
                        "categories: Sheet Mask, Lipstick, Foundation). Ensure active promotions "
                        "(Buy 2 Get 1 on PB, Buy 2 Get 2 on Sheet Mask, FLAT 40% OFF ON NON USM-1) "
                        "are prominently displayed. Brief kasthuri v and Shashikala to actively "
                        "guide customers from SKINCARE zone to billing counter."
                    ),
                    detected_at=now.isoformat() + "Z",
                    zone_id=None,
                    metric_value=round(today_rate, 4),
                    threshold_value=round(avg_7day, 4)
                ))

    # ── 3. Dead Zone (no visits in 30+ minutes) ──────────────────────
    cutoff = (now - timedelta(minutes=DEAD_ZONE_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = await db.execute(text("""
        SELECT zone_id, MAX(timestamp)
        FROM events
        WHERE store_id=:sid AND zone_id IS NOT NULL AND is_staff=0
          AND event_type IN ('ZONE_ENTER','ZONE_DWELL')
        GROUP BY zone_id
    """), {"sid": store_id})

    for zone_id, last_visit in result.fetchall():
        if last_visit and last_visit < cutoff:
            zone_name = ZONE_NAMES.get(zone_id, zone_id)
            anomalies.append(AnomalyItem(
                anomaly_id=str(uuid.uuid4()),
                anomaly_type="DEAD_ZONE",
                severity=AnomalySeverity.INFO,
                description=(
                    f"{zone_name} zone has had no customer visits in "
                    f"{DEAD_ZONE_MINUTES}+ minutes."
                ),
                suggested_action=(
                    f"Check if {zone_name} zone displays are visible from the main aisle. "
                    f"Consider repositioning promotional material — activate 'Buy 1 @ 10% Foxtale Event' "
                    f"signage near {zone_name} entrance. Assign Naziya Begum or Priya v to "
                    f"demonstrate a product near the zone entrance to attract foot traffic."
                ),
                detected_at=now.isoformat() + "Z",
                zone_id=zone_id,
                metric_value=None,
                threshold_value=float(DEAD_ZONE_MINUTES)
            ))

    # Also check zones that have NEVER been visited
    result = await db.execute(text("""
        SELECT DISTINCT zone_id FROM events WHERE store_id=:sid AND zone_id IS NOT NULL
    """), {"sid": store_id})
    visited_zones = {r[0] for r in result.fetchall()}
    all_zones = set(ZONE_NAMES.keys())
    never_visited = all_zones - visited_zones

    for zone_id in never_visited:
        zone_name = ZONE_NAMES.get(zone_id, zone_id)
        anomalies.append(AnomalyItem(
            anomaly_id=str(uuid.uuid4()),
            anomaly_type="DEAD_ZONE",
            severity=AnomalySeverity.WARN,
            description=f"{zone_name} zone has had no customer visits today.",
            suggested_action=(
                f"No traffic detected in {zone_name}. Verify camera coverage for this zone. "
                f"Consider placing a salesperson to actively promote products. "
                f"Check if 'B2@15% off Maybelline-BAU' promotion signage is visible."
            ),
            detected_at=now.isoformat() + "Z",
            zone_id=zone_id,
            metric_value=None,
            threshold_value=float(DEAD_ZONE_MINUTES)
        ))

    from config import get_store_info
    store_info = get_store_info(store_id)

    anom_res = AnomaliesResponse(
        store_id=store_id,
        store_name=store_info.get("store_name"),
        store_location=store_info.get("store_location"),
        city=store_info.get("city"),
        active_anomalies=anomalies,
        as_of=now.isoformat() + "Z"
    )
    if is_today:
        try:
            await cache.set(cache_key, anom_res.model_dump(), ex=2)
        except Exception:
            pass
    return anom_res
