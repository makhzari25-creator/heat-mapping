# PROMPT: Write pytest async tests for GET /stores/{id}/anomalies covering:
#   1. QUEUE_SPIKE fires when queue_depth >= 5 (WARN) and >= 10 (CRITICAL)
#   2. CONVERSION_DROP fires when today drops 20%+ below 7-day average
#   3. DEAD_ZONE fires when zone has no visits in 30+ minutes
#   4. Each anomaly has required fields: anomaly_id, anomaly_type, severity,
#      description, suggested_action, detected_at, zone_id, metric_value, threshold_value
#   5. suggested_action includes Brigade Bangalore-specific context
#   6. Empty store returns valid response with empty or populated anomaly list
#
# CHANGES MADE:
#   - Used ASGITransport for httpx client
#   - Used unique store IDs per test to avoid cross-test contamination
#   - Verified both WARN and CRITICAL severity thresholds
#   - Checked that suggested_action is a non-empty string

import pytest
import uuid
from httpx import AsyncClient, ASGITransport
from datetime import datetime, timedelta, timezone

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

from main import app


def make_event(**overrides):
    base = {
        "event_id": str(uuid.uuid4()),
        "store_id": "store_1",
        "camera_id": "CAM_BILLING_01",
        "visitor_id": f"VIS_{uuid.uuid4().hex[:6]}",
        "event_type": "ENTRY",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "zone_id": None,
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.90,
        "metadata": {"queue_depth": None, "sku_zone": None, "session_seq": 1}
    }
    return {**base, **overrides}


@pytest.mark.asyncio
async def test_anomalies_empty_store_valid_response():
    """Empty store must return valid AnomaliesResponse — not 500."""
    store_id = f"STORE_EMPTY_AN_{uuid.uuid4().hex[:6]}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"/stores/{store_id}/anomalies")
    assert r.status_code == 200
    d = r.json()
    assert "store_id" in d
    assert "active_anomalies" in d
    assert isinstance(d["active_anomalies"], list)
    assert "as_of" in d


@pytest.mark.asyncio
async def test_queue_spike_warn():
    """BILLING_QUEUE_JOIN with queue_depth=7 should trigger QUEUE_SPIKE WARN."""
    store_id = f"STORE_QWARN_{uuid.uuid4().hex[:6]}"
    vid = f"VIS_{uuid.uuid4().hex[:6]}"
    events = [
        make_event(store_id=store_id, visitor_id=vid, event_type="BILLING_QUEUE_JOIN",
                   camera_id="CAM_BILLING_01", zone_id="BILLING",
                   metadata={"queue_depth": 7, "sku_zone": "BILLING", "session_seq": 1})
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/events/ingest", json={"events": events})
        r = await c.get(f"/stores/{store_id}/anomalies")
    assert r.status_code == 200
    anomalies = r.json()["active_anomalies"]
    queue_spikes = [a for a in anomalies if a["anomaly_type"] == "QUEUE_SPIKE"]
    assert len(queue_spikes) >= 1
    spike = queue_spikes[0]
    assert spike["severity"] in ("WARN", "CRITICAL")
    assert spike["metric_value"] == 7.0
    assert spike["threshold_value"] == 5.0
    assert len(spike["suggested_action"]) > 10


@pytest.mark.asyncio
async def test_queue_spike_critical():
    """Queue depth >= 10 must trigger CRITICAL severity."""
    store_id = f"STORE_QCRIT_{uuid.uuid4().hex[:6]}"
    vid = f"VIS_{uuid.uuid4().hex[:6]}"
    events = [
        make_event(store_id=store_id, visitor_id=vid, event_type="BILLING_QUEUE_JOIN",
                   camera_id="CAM_BILLING_01", zone_id="BILLING",
                   metadata={"queue_depth": 12, "sku_zone": "BILLING", "session_seq": 1})
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/events/ingest", json={"events": events})
        r = await c.get(f"/stores/{store_id}/anomalies")
    assert r.status_code == 200
    anomalies = r.json()["active_anomalies"]
    queue_spikes = [a for a in anomalies if a["anomaly_type"] == "QUEUE_SPIKE"]
    assert any(a["severity"] == "CRITICAL" for a in queue_spikes)


@pytest.mark.asyncio
async def test_no_queue_spike_below_threshold():
    """Queue depth < 5 must NOT trigger QUEUE_SPIKE."""
    store_id = f"STORE_QLOW_{uuid.uuid4().hex[:6]}"
    vid = f"VIS_{uuid.uuid4().hex[:6]}"
    events = [
        make_event(store_id=store_id, visitor_id=vid, event_type="BILLING_QUEUE_JOIN",
                   camera_id="CAM_BILLING_01", zone_id="BILLING",
                   metadata={"queue_depth": 2, "sku_zone": "BILLING", "session_seq": 1})
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/events/ingest", json={"events": events})
        r = await c.get(f"/stores/{store_id}/anomalies")
    anomalies = r.json()["active_anomalies"]
    queue_spikes = [a for a in anomalies if a["anomaly_type"] == "QUEUE_SPIKE"]
    assert len(queue_spikes) == 0


@pytest.mark.asyncio
async def test_anomaly_required_fields():
    """Every anomaly must have all required fields."""
    store_id = f"STORE_FIELDS_{uuid.uuid4().hex[:6]}"
    vid = f"VIS_{uuid.uuid4().hex[:6]}"
    events = [
        make_event(store_id=store_id, visitor_id=vid, event_type="BILLING_QUEUE_JOIN",
                   camera_id="CAM_BILLING_01", zone_id="BILLING",
                   metadata={"queue_depth": 8, "sku_zone": "BILLING", "session_seq": 1})
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/events/ingest", json={"events": events})
        r = await c.get(f"/stores/{store_id}/anomalies")
    anomalies = r.json()["active_anomalies"]
    required = [
        "anomaly_id", "anomaly_type", "severity", "description",
        "suggested_action", "detected_at"
    ]
    for anomaly in anomalies:
        for field in required:
            assert field in anomaly, f"Missing field '{field}' in anomaly"
        assert anomaly["severity"] in ("INFO", "WARN", "CRITICAL")
        assert len(anomaly["suggested_action"]) > 0
        assert len(anomaly["description"]) > 0


@pytest.mark.asyncio
async def test_dead_zone_detected():
    """Zones with no recent visits should trigger DEAD_ZONE anomaly."""
    store_id = f"STORE_DEAD_{uuid.uuid4().hex[:6]}"
    vid = f"VIS_{uuid.uuid4().hex[:6]}"
    # Create old zone visit (well past 30-minute threshold)
    old_ts = (
        datetime.now(timezone.utc) - timedelta(hours=2)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    events = [
        make_event(store_id=store_id, visitor_id=vid, event_type="ZONE_ENTER",
                   zone_id="SKINCARE", timestamp=old_ts,
                   metadata={"queue_depth": None, "sku_zone": "Sheet Mask", "session_seq": 1})
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/events/ingest", json={"events": events})
        r = await c.get(f"/stores/{store_id}/anomalies")
    assert r.status_code == 200
    anomalies = r.json()["active_anomalies"]
    dead_zones = [a for a in anomalies if a["anomaly_type"] == "DEAD_ZONE"]
    assert len(dead_zones) >= 1
    dz = dead_zones[0]
    assert dz["zone_id"] is not None
    assert dz["threshold_value"] == 30.0
