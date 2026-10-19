"""
GET /stores/{store_id}/events — Raw event stream for live feed.
Returns the most recent N events for a store so the frontend
can display a real-time scrolling event log (no dummy data).
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from database import get_db
from datetime import datetime
import json

router = APIRouter()


@router.get("/stores/{store_id}/events")
async def get_store_events(
    store_id: str,
    limit: int = Query(default=50, le=500),
    since: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db)
):
    params = {"sid": store_id, "limit": limit}
    where = "WHERE store_id = :sid"

    if since:
        try:
            datetime.fromisoformat(since.replace("Z", "+00:00"))
            where += " AND timestamp >= :since"
            params["since"] = since
        except Exception:
            pass

    sql = f"""
        SELECT
            event_id, camera_id, visitor_id, event_type,
            timestamp, zone_id, dwell_ms, is_staff, confidence, metadata
        FROM events
        {where}
        ORDER BY timestamp DESC
        LIMIT :limit
    """

    result = await db.execute(text(sql), params)
    rows = result.fetchall()

    events = []
    for r in rows:
        meta_raw = r[9] if r[9] else "{}"
        try:
            meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
        except Exception:
            meta = {}
        events.append({
            "event_id": r[0],
            "camera_id": r[1],
            "visitor_id": r[2],
            "event_type": r[3],
            "timestamp": r[4],
            "zone_id": r[5],
            "dwell_ms": r[6],
            "is_staff": bool(r[7]),
            "confidence": r[8],
            "metadata": meta,
        })

    return {"store_id": store_id, "events": events, "count": len(events)}
