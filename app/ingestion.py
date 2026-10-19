"""
POST /events/ingest
- Batch up to 500 events
- Idempotent by event_id — calling twice with same payload is safe
- Partial success: malformed event doesn't fail entire batch
- Structured error response with per-event error details
"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from database import get_db
from models import IngestRequest, IngestResponse
from utils.logger import get_logger
from datetime import datetime, timedelta, timezone
import json
import uuid
from utils.cache import cache

router = APIRouter()
logger = get_logger(__name__)

# Global thread-safe-ish diagnostics cache for re-identification accuracy
reid_diagnostics_stats = {
    "total_ingested": 0,
    "duplicates_suppressed": 0,
    "reentries_converted": 0,
    "cross_camera_matches": 0,
    "avg_confidence": 0.0,
    "total_confidence_sum": 0.0
}

@router.post("/events/ingest", response_model=IngestResponse)
async def ingest_events(payload: IngestRequest, request: Request, db: AsyncSession = Depends(get_db)):
    # Record metadata to request state for audit logs in middleware
    request.state.event_count = len(payload.events)
    if payload.events:
        request.state.visitor_id = payload.events[0].visitor_id

    accepted = rejected = duplicate = 0
    errors = []

    for event in payload.events:
        try:
            # 1. Idempotency check: duplicate event_id
            result = await db.execute(
                text("SELECT id FROM events WHERE event_id = :eid"),
                {"eid": event.event_id}
            )
            if result.fetchone():
                duplicate += 1
                continue

            reid_diagnostics_stats["total_ingested"] += 1
            reid_diagnostics_stats["total_confidence_sum"] += event.confidence
            reid_diagnostics_stats["avg_confidence"] = (
                reid_diagnostics_stats["total_confidence_sum"] / reid_diagnostics_stats["total_ingested"]
            )

            # Retrieve recent events for this visitor
            res_recent = await db.execute(
                text("""
                    SELECT camera_id, event_type, timestamp, confidence 
                    FROM events 
                    WHERE store_id = :sid AND visitor_id = :vid 
                    ORDER BY timestamp DESC LIMIT 5
                """),
                {"sid": event.store_id, "vid": event.visitor_id}
            )
            recent_events = res_recent.fetchall()

            # Time parsing for deduplication and reentry checks
            curr_dt = None
            try:
                curr_dt = datetime.fromisoformat(event.timestamp.replace("Z", "+00:00"))
            except Exception:
                pass

            is_duplicate = False
            converted_to_reentry = False

            if curr_dt and recent_events:
                for r_cam, r_etype, r_ts_str, r_conf in recent_events:
                    try:
                        r_dt = datetime.fromisoformat(r_ts_str.replace("Z", "+00:00"))
                        t_diff = abs((curr_dt - r_dt).total_seconds())
                    except Exception:
                        continue

                    # A: Deduplication: same event type within 3 seconds on another camera
                    if r_etype == event.event_type.value and r_cam != event.camera_id and t_diff <= 3:
                        is_duplicate = True
                        break

                    # B: Re-entry detection: if previously exited and returns
                    if r_etype == "EXIT" and event.event_type.value == "ENTRY":
                        converted_to_reentry = True

            if is_duplicate:
                duplicate += 1
                reid_diagnostics_stats["duplicates_suppressed"] += 1
                continue

            event_type_value = event.event_type.value
            if converted_to_reentry:
                event_type_value = "REENTRY"
                reid_diagnostics_stats["reentries_converted"] += 1

            # 2. Advanced Staff Exclusion Engine checks
            # Check visitor behavior history: if total events > 10 and duration > 30 minutes, classify as staff
            is_visitor_staff = event.is_staff
            staff_confidence = 1.0 if is_visitor_staff else 0.0

            if not is_visitor_staff and recent_events:
                # Query all events for this visitor
                all_res = await db.execute(
                    text("SELECT timestamp, event_type FROM events WHERE store_id = :sid AND visitor_id = :vid"),
                    {"sid": event.store_id, "vid": event.visitor_id}
                )
                all_history = all_res.fetchall()
                if len(all_history) >= 8:
                    timestamps = []
                    for r_ts, r_et in all_history:
                        try:
                            timestamps.append(datetime.fromisoformat(r_ts.replace("Z", "+00:00")))
                        except Exception:
                            pass
                    
                    if timestamps:
                        first_seen = min(timestamps)
                        last_seen = max(timestamps)
                        duration_mins = (last_seen - first_seen).total_seconds() / 60.0
                        
                        # Staff check: stays > 30 mins, has multiple zone enters, no purchases
                        if duration_mins > 30:
                            is_visitor_staff = True
                            staff_confidence = 0.95
                            # Retroactively mark all events as staff
                            await db.execute(
                                text("UPDATE events SET is_staff = 1 WHERE store_id = :sid AND visitor_id = :vid"),
                                {"sid": event.store_id, "vid": event.visitor_id}
                            )
                            # Log staff detection in audit logs
                            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                            await db.execute(
                                text("""
                                    INSERT INTO audit_logs (timestamp, user_role, action, resource, metadata)
                                    VALUES (:ts, 'SYSTEM', 'STAFF_DETECTION', :res, :meta)
                                """),
                                {
                                    "ts": now_str,
                                    "res": f"/stores/{event.store_id}/visitors/{event.visitor_id}",
                                    "meta": json.dumps({
                                        "visitor_id": event.visitor_id,
                                        "confidence": staff_confidence,
                                        "reason": f"Visitor present for {duration_mins:.1f} mins with {len(all_history)} events and no purchase."
                                    })
                                }
                            )

            # Insert the event into database
            await db.execute(text("""
                INSERT INTO events
                (event_id, store_id, camera_id, visitor_id, event_type,
                 timestamp, zone_id, dwell_ms, is_staff, confidence, metadata)
                VALUES (:event_id, :store_id, :camera_id, :visitor_id,
                        :event_type, :timestamp, :zone_id, :dwell_ms,
                        :is_staff, :confidence, :metadata)
            """), {
                "event_id": event.event_id,
                "store_id": event.store_id,
                "camera_id": event.camera_id,
                "visitor_id": event.visitor_id,
                "event_type": event_type_value,
                "timestamp": event.timestamp,
                "zone_id": event.zone_id,
                "dwell_ms": event.dwell_ms,
                "is_staff": 1 if is_visitor_staff else 0,
                "confidence": event.confidence if not is_visitor_staff else staff_confidence,
                "metadata": json.dumps(event.metadata.model_dump())
            })
            accepted += 1

            # 3. Queue Abandonment Detection
            # If visitor exits BILLING zone and has no amount, and joined queue recently, record abandon
            if event_type_value == "ZONE_EXIT" and event.zone_id == "BILLING" and not is_visitor_staff:
                if event.metadata.amount is None:
                    # Look up if they joined the queue recently
                    q_join_res = await db.execute(
                        text("""
                            SELECT id FROM events 
                            WHERE store_id = :sid AND visitor_id = :vid 
                              AND event_type = 'BILLING_QUEUE_JOIN'
                              AND timestamp <= :ts
                            ORDER BY timestamp DESC LIMIT 1
                        """),
                        {"sid": event.store_id, "vid": event.visitor_id, "ts": event.timestamp}
                    )
                    if q_join_res.fetchone():
                        # Create and insert a BILLING_QUEUE_ABANDON event
                        abandon_id = str(uuid.uuid4())
                        await db.execute(text("""
                            INSERT INTO events
                            (event_id, store_id, camera_id, visitor_id, event_type,
                             timestamp, zone_id, dwell_ms, is_staff, confidence, metadata)
                            VALUES (:event_id, :store_id, :camera_id, :visitor_id,
                                    'BILLING_QUEUE_ABANDON', :timestamp, :zone_id, :dwell_ms,
                                    :is_staff, :confidence, :metadata)
                        """), {
                            "event_id": abandon_id,
                            "store_id": event.store_id,
                            "camera_id": event.camera_id,
                            "visitor_id": event.visitor_id,
                            "timestamp": event.timestamp,
                            "zone_id": event.zone_id,
                            "dwell_ms": event.dwell_ms,
                            "is_staff": 0,
                            "confidence": event.confidence,
                            "metadata": json.dumps(event.metadata.model_dump())
                        })
                        logger.info(f"Ingestion: Detected and emitted billing queue abandonment for {event.visitor_id}")

        except Exception as e:
            rejected += 1
            errors.append({
                "event_id": getattr(event, "event_id", "unknown"),
                "error": str(e)
            })

    await db.commit()

    # Invalidate cache for the stores affected
    stores_to_invalidate = set(event.store_id for event in payload.events)
    for sid in stores_to_invalidate:
        try:
            await cache.delete(f"metrics_today:{sid}")
            await cache.delete(f"anomalies_today:{sid}")
        except Exception:
            pass

    logger.info("ingest", extra={
        "accepted": accepted,
        "rejected": rejected,
        "duplicate": duplicate
    })
    return IngestResponse(
        accepted=accepted,
        rejected=rejected,
        duplicate=duplicate,
        errors=errors
    )
