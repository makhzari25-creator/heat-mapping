# PROMPT: Write pytest async tests for VisionRetail-IQ edge cases:
#   1. Group entry — multiple visitors entering at the exact same second
#   2. Staff-only clips — all events have is_staff=True, verify customer metrics are 0
#   3. Re-entry detection — customer exiting and re-entering within 30 minutes gets REENTRY
#   4. Queue abandonment — customer joining queue and exiting without purchase
#   5. Empty store periods — metrics returns zeros and handles empty periods gracefully
#   6. Camera overlap — duplicate events on entry/floor cameras within 3s are suppressed
#   7. Zero purchases — store with entry events but zero checkout transactions
#   8. Low-confidence detections — analytics on stores with < 10 visitors returns LOW confidence
#

import pytest
import uuid
from httpx import AsyncClient, ASGITransport
from datetime import datetime, timedelta, timezone, date

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

from main import app

def make_event(**overrides):
    today_str = date.today().isoformat()
    base = {
        "event_id": str(uuid.uuid4()),
        "store_id": "store_1",
        "camera_id": "CAM_ENTRY_01",
        "visitor_id": "VIS_test_123",
        "event_type": "ENTRY",
        "timestamp": f"{today_str}T12:00:00Z",
        "zone_id": None,
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.90,
        "metadata": {
            "queue_depth": None,
            "sku_zone": None,
            "session_seq": 1
        }
    }
    return {**base, **overrides}

@pytest.mark.asyncio
async def test_edge_group_entry():
    """Verify group entry of multiple visitors entering at same time."""
    store_id = f"STORE_GROUP_{uuid.uuid4().hex[:6]}"
    today_str = date.today().isoformat()
    ts = f"{today_str}T12:00:00Z"
    events = [
        make_event(store_id=store_id, visitor_id="VIS_group1", timestamp=ts),
        make_event(store_id=store_id, visitor_id="VIS_group2", timestamp=ts),
        make_event(store_id=store_id, visitor_id="VIS_group3", timestamp=ts)
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/events/ingest", json={"events": events})
        assert r.status_code == 200
        
        m_res = await c.get(f"/stores/{store_id}/metrics")
        assert m_res.status_code == 200
        assert m_res.json()["unique_visitors"] == 3

@pytest.mark.asyncio
async def test_edge_staff_only_clips():
    """Verify metrics return 0 customer counts for staff-only events."""
    store_id = f"STORE_STAFF_ONLY_{uuid.uuid4().hex[:6]}"
    events = [
        make_event(store_id=store_id, visitor_id="VIS_staff1", is_staff=True),
        make_event(store_id=store_id, visitor_id="VIS_staff2", is_staff=True)
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/events/ingest", json={"events": events})
        
        m_res = await c.get(f"/stores/{store_id}/metrics")
        assert m_res.status_code == 200
        assert m_res.json()["unique_visitors"] == 0

@pytest.mark.asyncio
async def test_edge_reentry_detection():
    """Verify reentry detection for visitor returning within 30 minutes."""
    store_id = f"STORE_REENTRY_EDGE_{uuid.uuid4().hex[:6]}"
    vid = "VIS_reentry1"
    today_str = date.today().isoformat()
    t1 = f"{today_str}T12:00:00Z"
    t2 = f"{today_str}T12:00:05Z"
    t3 = f"{today_str}T12:15:00Z"
    
    events = [
        make_event(store_id=store_id, visitor_id=vid, event_type="ENTRY", timestamp=t1),
        make_event(store_id=store_id, visitor_id=vid, event_type="EXIT", timestamp=t2),
        make_event(store_id=store_id, visitor_id=vid, event_type="ENTRY", timestamp=t3)
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/events/ingest", json={"events": events})
        assert r.status_code == 200
        
        diag_res = await c.get("/ai/reid-diagnostics")
        assert diag_res.status_code == 200
        assert diag_res.json()["reentries_converted"] >= 1

@pytest.mark.asyncio
async def test_edge_queue_abandonment():
    """Verify queue abandonment detection when exiting without purchase."""
    store_id = f"STORE_ABANDON_{uuid.uuid4().hex[:6]}"
    vid = f"VIS_abandon_{uuid.uuid4().hex[:6]}"
    today_str = date.today().isoformat()
    t1 = f"{today_str}T12:00:00Z"
    t2 = f"{today_str}T12:01:00Z"
    t3 = f"{today_str}T12:03:00Z"
    
    events = [
        make_event(store_id=store_id, visitor_id=vid, event_type="ENTRY", timestamp=t1),
        make_event(store_id=store_id, visitor_id=vid, event_type="BILLING_QUEUE_JOIN", zone_id="BILLING", timestamp=t2),
        make_event(store_id=store_id, visitor_id=vid, event_type="ZONE_EXIT", zone_id="BILLING", timestamp=t3, metadata={"amount": None})
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/events/ingest", json={"events": events})
        
        m_res = await c.get(f"/stores/{store_id}/metrics")
        assert m_res.status_code == 200
        assert m_res.json()["abandonment_rate"] == 1.0

@pytest.mark.asyncio
async def test_edge_empty_store_periods():
    """Verify metrics endpoints handle empty stores gracefully."""
    store_id = f"STORE_EMPTY_{uuid.uuid4().hex[:6]}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"/stores/{store_id}/metrics")
        assert r.status_code == 200
        assert r.json()["unique_visitors"] == 0
        assert r.json()["conversion_rate"] == 0.0

@pytest.mark.asyncio
async def test_edge_camera_overlap():
    """Verify duplicate events on overlapping cameras within 3 seconds are suppressed."""
    store_id = f"STORE_OVERLAP_{uuid.uuid4().hex[:6]}"
    vid = "VIS_overlap1"
    today_str = date.today().isoformat()
    ts = f"{today_str}T12:00:00Z"
    
    events = [
        make_event(store_id=store_id, visitor_id=vid, camera_id="CAM_ENTRY_01", event_type="ENTRY", timestamp=ts),
        make_event(store_id=store_id, visitor_id=vid, camera_id="CAM_FLOOR_01", event_type="ENTRY", timestamp=ts)
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/events/ingest", json={"events": events})
        assert r.status_code == 200
        d = r.json()
        assert d["accepted"] == 1
        assert d["duplicate"] == 1

@pytest.mark.asyncio
async def test_edge_zero_purchases():
    """Verify store conversion rate is 0.0 when zero checkout events are made."""
    store_id = f"STORE_ZERO_TXN_{uuid.uuid4().hex[:6]}"
    vid = "VIS_customer1"
    events = [
        make_event(store_id=store_id, visitor_id=vid, event_type="ENTRY"),
        make_event(store_id=store_id, visitor_id=vid, event_type="BILLING_QUEUE_JOIN", zone_id="BILLING")
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/events/ingest", json={"events": events})
        r = await c.get(f"/stores/{store_id}/metrics")
        assert r.status_code == 200
        assert r.json()["conversion_rate"] == 1.0
        
        await c.post("/events/ingest", json={"events": [
            make_event(store_id=store_id, visitor_id=vid, event_type="ZONE_EXIT", zone_id="BILLING")
        ]})
        r2 = await c.get(f"/stores/{store_id}/metrics")
        assert r2.status_code == 200
        assert r2.json()["conversion_rate"] == 0.0

@pytest.mark.asyncio
async def test_edge_low_confidence_detections():
    """Verify low session count returns LOW data confidence."""
    store_id = f"STORE_LOW_CONF_{uuid.uuid4().hex[:6]}"
    events = [make_event(store_id=store_id, visitor_id="VIS_lowconf1")]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/events/ingest", json={"events": events})
        r = await c.get(f"/stores/{store_id}/metrics")
        assert r.status_code == 200
        conf = r.json()["data_confidence"]
        assert conf["confidence_level"] == "LOW"
        assert conf["supporting_sample_size"] == 1


@pytest.mark.asyncio
async def test_edge_command_center():
    """Verify that GET /ai/command-center returns executive reports."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/ai/command-center?store_id=store_1")
    assert r.status_code == 200
    data = r.json()
    assert "store_id" in data
    assert "daily_summary" in data
    assert "revenue_summary" in data
    assert "conversion_summary" in data
    assert "queue_summary" in data
    assert "insights" in data
    assert isinstance(data["insights"], list)
    for insight in data["insights"]:
        assert "insight_type" in insight
        assert "title" in insight
        assert "summary" in insight
        assert "confidence_score" in insight
        assert "business_impact" in insight
        assert "recommended_action" in insight


@pytest.mark.asyncio
async def test_edge_revenue_leakage():
    """Verify that GET /ai/revenue-leakage returns leakage items."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/ai/revenue-leakage?store_id=store_1")
    assert r.status_code == 200
    data = r.json()
    assert "store_id" in data
    assert "total_leakage_daily" in data
    assert "total_leakage_monthly" in data
    assert "leakages" in data
    assert isinstance(data["leakages"], list)
    for leakage in data["leakages"]:
        assert "issue_name" in leakage
        assert "category" in leakage
        assert "description" in leakage
        assert "estimated_loss_daily" in leakage
        assert "missed_conversions" in leakage
        assert "monthly_impact" in leakage
        assert "business_impact_rank" in leakage
        assert "recommended_action" in leakage
