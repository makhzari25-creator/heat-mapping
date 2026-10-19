"""
Conversion funnel — Brigade Bangalore reality:
Entry -> Zone Visit -> Billing Queue -> Purchase

Critical: unit is SESSION (visitor_id), NOT raw event count.
REENTRY events must NOT add a second entry to the funnel count.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from database import get_db
from models import FunnelResponse, FunnelStage
from datetime import date

router = APIRouter()


@router.get("/stores/{store_id}/funnel", response_model=FunnelResponse)
async def get_funnel(
    store_id: str,
    date_str: str = None,
    db: AsyncSession = Depends(get_db)
):
    target_date = date_str or date.today().isoformat()
    t_start = f"{target_date}T00:00:00"
    t_end = f"{target_date}T23:59:59.999Z"

    # Stage 1: ENTRY only (exclude REENTRY to avoid double-counting)
    result = await db.execute(text("""
        SELECT COUNT(DISTINCT visitor_id)
        FROM events
        WHERE store_id=:sid AND event_type='ENTRY'
          AND is_staff=0 AND timestamp >= :t_start AND timestamp <= :t_end
    """), {"sid": store_id, "t_start": t_start, "t_end": t_end})
    entry_count = result.scalar() or 0

    # Stage 2: Visited a product zone (non-billing)
    result = await db.execute(text("""
        SELECT COUNT(DISTINCT visitor_id)
        FROM events
        WHERE store_id=:sid AND event_type='ZONE_ENTER'
          AND is_staff=0 AND zone_id IS NOT NULL
          AND zone_id NOT IN ('BILLING','CHECKOUT')
          AND timestamp >= :t_start AND timestamp <= :t_end
          AND visitor_id IN (
              SELECT DISTINCT visitor_id FROM events
              WHERE store_id=:sid AND event_type='ENTRY'
                AND is_staff=0 AND timestamp >= :t_start AND timestamp <= :t_end
          )
    """), {"sid": store_id, "t_start": t_start, "t_end": t_end})
    zone_count = result.scalar() or 0

    # Stage 3: Reached billing queue
    result = await db.execute(text("""
        SELECT COUNT(DISTINCT visitor_id)
        FROM events
        WHERE store_id=:sid AND event_type='BILLING_QUEUE_JOIN'
          AND is_staff=0 AND timestamp >= :t_start AND timestamp <= :t_end
    """), {"sid": store_id, "t_start": t_start, "t_end": t_end})
    billing_count = result.scalar() or 0

    # Stage 4: Purchased (billing minus abandons)
    result = await db.execute(text("""
        SELECT COUNT(DISTINCT visitor_id)
        FROM events
        WHERE store_id=:sid AND event_type='BILLING_QUEUE_ABANDON'
          AND is_staff=0 AND timestamp >= :t_start AND timestamp <= :t_end
    """), {"sid": store_id, "t_start": t_start, "t_end": t_end})
    abandon_count = result.scalar() or 0
    purchase_count = max(0, billing_count - abandon_count)

    def drop_off(current: int, previous: int) -> float:
        return round((1 - current / previous) * 100, 1) if previous > 0 else 0.0

    sample_size = entry_count
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

    return FunnelResponse(
        store_id=store_id,
        store_name=store_info.get("store_name"),
        store_location=store_info.get("store_location"),
        city=store_info.get("city"),
        date=target_date,
        stages=[
            FunnelStage(stage="Entry", count=entry_count, drop_off_pct=0.0),
            FunnelStage(
                stage="Zone Visit",
                count=zone_count,
                drop_off_pct=drop_off(zone_count, entry_count)
            ),
            FunnelStage(
                stage="Billing Queue",
                count=billing_count,
                drop_off_pct=drop_off(billing_count, zone_count)
            ),
            FunnelStage(
                stage="Purchase",
                count=purchase_count,
                drop_off_pct=drop_off(purchase_count, billing_count)
            ),
        ],
        total_sessions=entry_count,
        data_confidence=confidence_data
    )
