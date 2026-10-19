"""
Real-time store metrics.

Brigade Bangalore reality check:
- 24 orders on 10-04-2026, Rs 44,920 GMV
- 5 staff (is_staff=true) excluded from all customer counts
- Handle zero-purchase stores: return 0.0, never null, never 500
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from database import get_db
from models import StoreMetrics, ZoneDwell
from datetime import date, datetime
import json
from utils.cache import cache

router = APIRouter()

ZONE_NAMES = {
    "MAKEUP": "Makeup & Colour",
    "SKINCARE": "Skincare & Treatments",
    "BATH_BODY": "Bath & Body",
    "HAIR": "Hair Care",
    "FRAGRANCE": "Fragrance",
    "PERSONAL_CARE": "Personal Care",
    "BILLING": "Billing Counter",
}


@router.get("/stores/{store_id}/metrics", response_model=StoreMetrics)
async def get_store_metrics(
    store_id: str,
    date_str: str = None,
    db: AsyncSession = Depends(get_db)
):
    target_date = date_str or date.today().isoformat()
    is_today = date_str is None or date_str == date.today().isoformat()
    cache_key = f"metrics_today:{store_id}"
    
    if is_today:
        try:
            cached = await cache.get(cache_key)
            if cached:
                return StoreMetrics(**cached)
        except Exception:
            pass

    t_start = f"{target_date}T00:00:00"
    t_end = f"{target_date}T23:59:59.999Z"

    # Unique customer visitors (ENTRY, not staff)
    result = await db.execute(text("""
        SELECT COUNT(DISTINCT visitor_id)
        FROM events
        WHERE store_id = :sid AND event_type = 'ENTRY'
          AND is_staff = 0 AND timestamp >= :t_start AND timestamp <= :t_end
    """), {"sid": store_id, "t_start": t_start, "t_end": t_end})
    unique_visitors = result.scalar() or 0

    # Billing queue joins and abandons
    result = await db.execute(text("""
        SELECT
            COUNT(DISTINCT CASE WHEN event_type='BILLING_QUEUE_JOIN' THEN visitor_id END),
            COUNT(DISTINCT CASE WHEN event_type='BILLING_QUEUE_ABANDON' THEN visitor_id END)
        FROM events
        WHERE store_id=:sid AND is_staff=0 AND timestamp >= :t_start AND timestamp <= :t_end
    """), {"sid": store_id, "t_start": t_start, "t_end": t_end})
    row = result.fetchone()
    billing_count = row[0] or 0
    abandoned_count = row[1] or 0

    conversion_rate = (
        (billing_count - abandoned_count) / unique_visitors
        if unique_visitors > 0 else 0.0
    )

    # Average dwell per zone
    result = await db.execute(text("""
        SELECT zone_id, AVG(dwell_ms)/60000.0, COUNT(DISTINCT visitor_id)
        FROM events
        WHERE store_id=:sid AND event_type='ZONE_DWELL'
          AND is_staff=0 AND zone_id IS NOT NULL AND timestamp >= :t_start AND timestamp <= :t_end
        GROUP BY zone_id
    """), {"sid": store_id, "t_start": t_start, "t_end": t_end})
    zone_dwells = [
        ZoneDwell(
            zone_id=r[0],
            zone_name=ZONE_NAMES.get(r[0], r[0]),
            avg_dwell_minutes=round(r[1] or 0, 2),
            visitor_count=r[2] or 0
        )
        for r in result.fetchall()
    ]

    # Current queue depth from latest BILLING_QUEUE_JOIN event
    result = await db.execute(text("""
        SELECT metadata FROM events
        WHERE store_id=:sid AND event_type='BILLING_QUEUE_JOIN'
        ORDER BY timestamp DESC LIMIT 1
    """), {"sid": store_id})
    row = result.fetchone()
    current_queue = 0
    if row:
        try:
            meta = json.loads(row[0])
            current_queue = meta.get("queue_depth") or 0
        except Exception:
            pass

    abandonment_rate = abandoned_count / billing_count if billing_count > 0 else 0.0

    # Calculate dynamic store financials
    result = await db.execute(text("""
        SELECT metadata FROM events
        WHERE store_id = :sid AND zone_id = 'BILLING' 
          AND event_type = 'ZONE_EXIT' AND is_staff = 0 
          AND timestamp >= :t_start AND timestamp <= :t_end
    """), {"sid": store_id, "t_start": t_start, "t_end": t_end})
    
    total_gmv = 0.0
    total_orders = 0
    
    for r in result.fetchall():
        if r[0]:
            try:
                meta = json.loads(r[0]) if isinstance(r[0], str) else r[0]
                amount = meta.get("amount")
                if amount is not None:
                    total_gmv += float(amount)
                    total_orders += 1
            except Exception:
                pass

    avg_basket_value = total_gmv / total_orders if total_orders > 0 else 0.0

    result = await db.execute(
        text("SELECT MAX(timestamp) FROM events WHERE store_id=:sid"),
        {"sid": store_id}
    )
    last_ts = result.scalar() or datetime.utcnow().isoformat() + "Z"

    # Determine data confidence based on visitor count sample size
    sample_size = unique_visitors
    if sample_size >= 20:
        conf_level = "HIGH"
        conf_score = 0.95
    elif sample_size >= 10:
        conf_level = "MEDIUM"
        conf_score = 0.80
    else:
        conf_level = "LOW"
        conf_score = 0.45

    from models import DataConfidence
    confidence_data = DataConfidence(
        confidence_score=conf_score,
        confidence_level=conf_level,
        supporting_sample_size=sample_size
    )

    from config import get_store_info
    store_info = get_store_info(store_id)

    metrics_res = StoreMetrics(
        store_id=store_id,
        store_name=store_info.get("store_name"),
        store_location=store_info.get("store_location"),
        city=store_info.get("city"),
        date=target_date,
        unique_visitors=unique_visitors,
        conversion_rate=round(max(0.0, conversion_rate), 4),
        avg_dwell_per_zone=zone_dwells,
        current_queue_depth=current_queue,
        abandonment_rate=round(abandonment_rate, 4),
        as_of=last_ts,
        total_gmv=round(total_gmv, 2),
        total_orders=total_orders,
        avg_basket_value=round(avg_basket_value, 2),
        data_confidence=confidence_data
    )
    if is_today:
        try:
            await cache.set(cache_key, metrics_res.model_dump(), ex=2)
        except Exception:
            pass
    return metrics_res
