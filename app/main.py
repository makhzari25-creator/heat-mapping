from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import time
import uuid
import json
import uvicorn

from database import init_db, AsyncSessionLocal
from config import settings
from utils.logger import get_logger
from ingestion import router as ingest_router
from metrics import router as metrics_router
from funnel import router as funnel_router
from heatmap import router as heatmap_router
from anomalies import router as anomaly_router
from health import router as health_router
from config_router import router as config_router
from events_router import router as events_router
from simulator import router as simulator_router, run_live_simulator
from ai_router import router as ai_router

logger = get_logger(__name__)


async def _seed_if_empty():
    """Seed the database with dynamic events on first startup if empty or if today is under-seeded."""
    try:
        from sqlalchemy import text
        from datetime import date
        async with AsyncSessionLocal() as db:
            result = await db.execute(text("SELECT COUNT(*) FROM events"))
            count = result.scalar() or 0
            
            # Check if today has checkout/billing exits with amount
            today_str = date.today().isoformat()
            result_today_total = await db.execute(
                text("SELECT COUNT(*) FROM events WHERE timestamp LIKE :today"),
                {"today": f"{today_str}%"}
            )
            today_count = result_today_total.scalar() or 0

            result_today = await db.execute(
                text("""
                    SELECT COUNT(*) FROM events 
                    WHERE store_id = 'store_1' AND event_type = 'ZONE_EXIT' 
                      AND zone_id = 'BILLING' AND timestamp LIKE :today 
                      AND metadata LIKE '%amount%'
                """),
                {"today": f"{today_str}%"}
            )
            today_checkouts = result_today.scalar() or 0

        if count == 0 or today_count < 200 or today_checkouts < 10:
            from startup_seed import generate_seed_events
            
            events = []
            if count == 0:
                logger.info("Database empty — seeding historical Store 1 and Store 2 events")
                # Generate events for Store 1 (Brigade Bangalore)
                hist_events_blr = generate_seed_events(date(2026, 4, 10), "store_1")
                # Generate events for Store 2 (Colaba Mumbai)
                hist_events_mum = generate_seed_events(date(2026, 4, 10), "store_2")
                events += hist_events_blr + hist_events_mum
                
            if today_count < 200 or today_checkouts < 10:
                logger.info("Today under-seeded or lacks checkouts — clearing and seeding today's events for Store 1 and Store 2")
                # Clear existing events for today
                async with AsyncSessionLocal() as db:
                    await db.execute(
                        text("DELETE FROM events WHERE timestamp LIKE :today"),
                        {"today": f"{today_str}%"}
                    )
                    await db.commit()
                
                today_events_blr = generate_seed_events(date.today(), "store_1")
                today_events_mum = generate_seed_events(date.today(), "store_2")
                events += today_events_blr + today_events_mum
                
            if not events:
                logger.warning("No seed events generated — POS data may be missing")
                return

            # Batch insert (chunks of 200)
            async with AsyncSessionLocal() as db:
                inserted = 0
                for i in range(0, len(events), 200):
                    batch = events[i:i+200]
                    params_list = []
                    for event in batch:
                        params_list.append({
                            "event_id": event["event_id"],
                            "store_id": event["store_id"],
                            "camera_id": event["camera_id"],
                            "visitor_id": event["visitor_id"],
                            "event_type": event["event_type"],
                            "timestamp": event["timestamp"],
                            "zone_id": event.get("zone_id"),
                            "dwell_ms": event.get("dwell_ms", 0),
                            "is_staff": int(event.get("is_staff", False)),
                            "confidence": event.get("confidence", 0.85),
                            "metadata": json.dumps(event.get("metadata", {}))
                        })
                    try:
                        await db.execute(text("""
                            INSERT OR IGNORE INTO events
                            (event_id, store_id, camera_id, visitor_id, event_type,
                             timestamp, zone_id, dwell_ms, is_staff, confidence, metadata)
                            VALUES (:event_id, :store_id, :camera_id, :visitor_id,
                                    :event_type, :timestamp, :zone_id, :dwell_ms,
                                    :is_staff, :confidence, :metadata)
                        """), params_list)
                        await db.commit()
                        inserted += len(batch)
                    except Exception as e:
                        logger.warning(f"Batch insert failed: {e}")

            logger.info(f"Seeded {inserted} events into database")

            # Log summary
            unique_customers = len({e["visitor_id"] for e in events
                                     if not e.get("is_staff", False)
                                     and e["event_type"] == "ENTRY"})
            logger.info(f"Seed summary: {unique_customers} unique customer sessions")
        else:
            logger.info(f"Database already has {count} events and today has {today_count} events — skipping seed")
    except Exception as e:
        logger.warning(f"Seed failed (non-fatal): {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting VisionRetail-IQ API — Store 1 (Brigade Bangalore)")
    await init_db()
    await _seed_if_empty()
    
    # Start live shopper simulator in the background
    import asyncio
    sim_task = asyncio.create_task(run_live_simulator())
    
    yield
    
    # Shut down simulator
    from simulator import sim_state
    sim_state.running = False
    sim_task.cancel()
    try:
        await sim_task
    except asyncio.CancelledError:
        pass
    logger.info("Shutdown complete")


app = FastAPI(
    title="VisionRetail-IQ API",
    version="1.0.0",
    description=(
        "Real-time retail analytics for VisionRetail-IQ's Brigade Bangalore store. "
        "All metrics are computed dynamically from ingested CV event data."
    ),
    lifespan=lifespan
)

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    trace_id = str(uuid.uuid4())[:8]
    store_id = request.path_params.get("store_id", "-")
    if store_id == "-":
        # Try query params or path segments
        store_id = request.query_params.get("store_id", "-")
        if store_id == "-":
            parts = request.url.path.split("/")
            for p in parts:
                if p.startswith("STORE_"):
                    store_id = p
                    break

    # Initialize state values to avoid missing attributes
    request.state.visitor_id = None
    request.state.event_count = 0

    # Query params fallback
    visitor_id_q = request.query_params.get("visitor_id")
    if visitor_id_q:
        request.state.visitor_id = visitor_id_q
    else:
        parts = request.url.path.split("/")
        for p in parts:
            if p.startswith("VIS_"):
                request.state.visitor_id = p
                break

    start = time.perf_counter()
    response = await call_next(request)
    latency_ms = round((time.perf_counter() - start) * 1000, 2)
    
    # Retrieve updated values from state (which may have been populated by endpoint handlers)
    visitor_id = getattr(request.state, "visitor_id", None)
    event_count = getattr(request.state, "event_count", 0)

    # Track latency for health monitoring
    try:
        from health import record_latency
        record_latency(latency_ms)
    except Exception:
        pass

    # Structured stdout logging
    logger.info("request", extra={
        "trace_id": trace_id,
        "store_id": store_id,
        "visitor_id": visitor_id,
        "endpoint": str(request.url.path),
        "method": request.method,
        "latency_ms": latency_ms,
        "status_code": response.status_code,
        "event_count": event_count
    })

    # Searchable Database Audit Log insertion
    try:
        from utils.audit import log_action
        await log_action(
            user_role="API_GATEWAY",
            action=request.method,
            resource=str(request.url.path),
            metadata={
                "trace_id": trace_id,
                "store_id": store_id,
                "visitor_id": visitor_id,
                "latency_ms": latency_ms,
                "status_code": response.status_code,
                "event_count": event_count
            }
        )
    except Exception:
        pass

    response.headers["X-Trace-ID"] = trace_id
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_server_error",
            "detail": "An unexpected error occurred."
        }
    )


app.include_router(config_router)
app.include_router(events_router)
app.include_router(ingest_router)
app.include_router(metrics_router)
app.include_router(funnel_router)
app.include_router(heatmap_router)
app.include_router(anomaly_router)
app.include_router(health_router)
app.include_router(simulator_router)
app.include_router(ai_router)


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
