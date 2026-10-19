# PROMPT: Write pytest async tests for GET /stores/{id}/metrics covering:
#   1. Empty store returns 0 unique_visitors, 0.0 rates — never null, never 500
#   2. Staff events excluded from customer counts
#   3. Conversion rate = (billing_joins - abandons) / unique_visitors
#   4. Zone dwell aggregation correctness
#   5. Current queue depth from most recent BILLING_QUEUE_JOIN metadata
#   6. date_str parameter filters by date correctly
#
# CHANGES MADE:
#   - Used store_1 as primary store per schema spec
#   - Used ASGITransport instead of deprecated app= parameter in httpx
#   - Added unique store IDs per test to avoid cross-test contamination
#   - Verified as_of field is present in response

import pytest
import uuid
import json
from httpx import AsyncClient, ASGITransport

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

from main import app


def make_event(**overrides):
    base = {
        "event_id": str(uuid.uuid4()),
        "store_id": "store_1",
        "camera_id": "CAM_ENTRY_01",
        "visitor_id": "VIS_abc123",
        "event_type": "ENTRY",
        "timestamp": "2026-04-10T07:15:00Z",
        "zone_id": None,
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.88,
        "metadata": {"queue_depth": None, "sku_zone": None, "session_seq": 1}
    }
    return {**base, **overrides}


@pytest.mark.asyncio
async def test_metrics_empty_store():
    """Empty store must return 0/0.0 — never null, never 500."""
    store_id = f"STORE_EMPTY_{uuid.uuid4().hex[:6]}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"/stores/{store_id}/metrics")
    assert r.status_code == 200
    d = r.json()
    assert d["unique_visitors"] == 0
    assert d["conversion_rate"] == 0.0
    assert d["abandonment_rate"] == 0.0
    assert d["current_queue_depth"] == 0
    assert isinstance(d["avg_dwell_per_zone"], list)
    assert "as_of" in d


@pytest.mark.asyncio
async def test_metrics_staff_excluded():
    """Staff events (is_staff=True) must NOT count toward unique_visitors."""
    store_id = f"STORE_STAFF_{uuid.uuid4().hex[:6]}"
    events = [
        make_event(store_id=store_id, visitor_id=f"VIS_{uuid.uuid4().hex[:6]}",
                   is_staff=True),   # staff — excluded
        make_event(store_id=store_id, visitor_id=f"VIS_{uuid.uuid4().hex[:6]}",
                   is_staff=False),  # customer — counted
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/events/ingest", json={"events": events})
        r = await c.get(f"/stores/{store_id}/metrics", params={"date_str": "2026-04-10"})
    assert r.status_code == 200
    d = r.json()
    assert d["unique_visitors"] == 1  # Only the non-staff customer


@pytest.mark.asyncio
async def test_metrics_conversion_rate():
    """Conversion rate = (billing_joins - abandons) / unique_visitors."""
    store_id = f"STORE_CR_{uuid.uuid4().hex[:6]}"
    vid1 = f"VIS_{uuid.uuid4().hex[:6]}"
    vid2 = f"VIS_{uuid.uuid4().hex[:6]}"
    vid3 = f"VIS_{uuid.uuid4().hex[:6]}"

    events = [
        # 3 customers enter
        make_event(store_id=store_id, visitor_id=vid1),
        make_event(store_id=store_id, visitor_id=vid2),
        make_event(store_id=store_id, visitor_id=vid3),
        # 2 join billing queue
        make_event(store_id=store_id, visitor_id=vid1, event_type="BILLING_QUEUE_JOIN",
                   zone_id="BILLING",
                   metadata={"queue_depth": 1, "sku_zone": "BILLING", "session_seq": 2}),
        make_event(store_id=store_id, visitor_id=vid2, event_type="BILLING_QUEUE_JOIN",
                   zone_id="BILLING",
                   metadata={"queue_depth": 2, "sku_zone": "BILLING", "session_seq": 2}),
        # 1 abandons
        make_event(store_id=store_id, visitor_id=vid2, event_type="BILLING_QUEUE_ABANDON",
                   zone_id="BILLING",
                   metadata={"queue_depth": None, "sku_zone": "BILLING", "session_seq": 3}),
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/events/ingest", json={"events": events})
        r = await c.get(f"/stores/{store_id}/metrics", params={"date_str": "2026-04-10"})
    assert r.status_code == 200
    d = r.json()
    # 3 visitors, 2 joined billing, 1 abandoned → (2-1)/3 = 0.333...
    assert d["unique_visitors"] == 3
    assert abs(d["conversion_rate"] - 1/3) < 0.01
    assert d["abandonment_rate"] == 0.5


@pytest.mark.asyncio
async def test_metrics_zone_dwell():
    """Zone dwell should aggregate correctly per zone."""
    store_id = f"STORE_DWELL_{uuid.uuid4().hex[:6]}"
    vid = f"VIS_{uuid.uuid4().hex[:6]}"
    events = [
        make_event(store_id=store_id, visitor_id=vid),
        make_event(store_id=store_id, visitor_id=vid, event_type="ZONE_DWELL",
                   zone_id="SKINCARE", dwell_ms=60000,
                   metadata={"queue_depth": None, "sku_zone": "Sheet Mask", "session_seq": 2}),
        make_event(store_id=store_id, visitor_id=vid, event_type="ZONE_DWELL",
                   zone_id="MAKEUP", dwell_ms=30000,
                   metadata={"queue_depth": None, "sku_zone": "Lipstick", "session_seq": 3}),
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/events/ingest", json={"events": events})
        r = await c.get(f"/stores/{store_id}/metrics", params={"date_str": "2026-04-10"})
    assert r.status_code == 200
    d = r.json()
    zone_ids = {z["zone_id"] for z in d["avg_dwell_per_zone"]}
    assert "SKINCARE" in zone_ids
    assert "MAKEUP" in zone_ids
    skincare = next(z for z in d["avg_dwell_per_zone"] if z["zone_id"] == "SKINCARE")
    assert abs(skincare["avg_dwell_minutes"] - 1.0) < 0.1


@pytest.mark.asyncio
async def test_metrics_queue_depth():
    """Current queue depth should reflect latest BILLING_QUEUE_JOIN metadata."""
    store_id = f"STORE_QUEUE_{uuid.uuid4().hex[:6]}"
    vid = f"VIS_{uuid.uuid4().hex[:6]}"
    events = [
        make_event(store_id=store_id, visitor_id=vid),
        make_event(store_id=store_id, visitor_id=vid, event_type="BILLING_QUEUE_JOIN",
                   zone_id="BILLING",
                   metadata={"queue_depth": 7, "sku_zone": "BILLING", "session_seq": 2}),
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/events/ingest", json={"events": events})
        r = await c.get(f"/stores/{store_id}/metrics")
    assert r.status_code == 200
    d = r.json()
    assert d["current_queue_depth"] == 7


@pytest.mark.asyncio
async def test_metrics_response_schema():
    """Response must have all required fields with correct types."""
    store_id = f"STORE_SCHEMA_{uuid.uuid4().hex[:6]}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"/stores/{store_id}/metrics")
    assert r.status_code == 200
    d = r.json()
    required_fields = [
        "store_id", "date", "unique_visitors", "conversion_rate",
        "avg_dwell_per_zone", "current_queue_depth", "abandonment_rate", "as_of",
        "total_gmv", "total_orders", "avg_basket_value"
    ]
    for field in required_fields:
        assert field in d, f"Missing field: {field}"
    assert isinstance(d["unique_visitors"], int)
    assert isinstance(d["conversion_rate"], float)
    assert isinstance(d["avg_dwell_per_zone"], list)
    assert isinstance(d["total_gmv"], (int, float))
    assert isinstance(d["total_orders"], int)
    assert isinstance(d["avg_basket_value"], (int, float))
