import pytest
import uuid
from httpx import AsyncClient, ASGITransport
from datetime import datetime, timezone

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

from main import app
from simulator import sim_state

@pytest.mark.asyncio
async def test_simulator_status():
    """Assert that GET /simulator/status returns the correct structure."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/simulator/status")
    
    assert r.status_code == 200
    data = r.json()
    assert "running" in data
    assert "active_visitors_count" in data
    assert "queue_depth" in data
    assert "overrides" in data
    assert isinstance(data["overrides"], dict)
    assert "trigger_queue_spike" in data["overrides"]
    assert "trigger_conversion_drop" in data["overrides"]
    assert "frozen_dead_zone" in data["overrides"]


@pytest.mark.asyncio
async def test_simulator_control_overrides():
    """Assert that simulator control endpoints successfully activate overrides and reset."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        # 1. Trigger queue spike
        r1 = await c.post("/simulator/control", json={"action": "trigger_queue_spike"})
        assert r1.status_code == 200
        d1 = r1.json()
        assert d1["status"] == "ok"
        assert d1["simulator_state"]["overrides"]["trigger_queue_spike"] is True
        assert sim_state.trigger_queue_spike.get("store_1") is True

        # 2. Trigger conversion drop
        r2 = await c.post("/simulator/control", json={"action": "trigger_conversion_drop"})
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["status"] == "ok"
        assert d2["simulator_state"]["overrides"]["trigger_conversion_drop"] is True
        assert sim_state.trigger_conversion_drop.get("store_1") is True

        # 3. Freeze a specific zone (Dead Zone)
        r3 = await c.post("/simulator/control", json={"action": "freeze_zone", "zone_id": "FRAGRANCE"})
        assert r3.status_code == 200
        d3 = r3.json()
        assert d3["status"] == "ok"
        assert d3["simulator_state"]["overrides"]["frozen_dead_zone"] == "FRAGRANCE"
        assert sim_state.frozen_dead_zone.get("store_1") == "FRAGRANCE"

        # 4. Reset overrides
        r4 = await c.post("/simulator/control", json={"action": "reset"})
        assert r4.status_code == 200
        d4 = r4.json()
        assert d4["status"] == "ok"
        assert d4["simulator_state"]["overrides"]["trigger_queue_spike"] is False
        assert d4["simulator_state"]["overrides"]["trigger_conversion_drop"] is False
        assert d4["simulator_state"]["overrides"]["frozen_dead_zone"] is None
        assert sim_state.trigger_queue_spike.get("store_1") is False
        assert sim_state.trigger_conversion_drop.get("store_1") is False
        assert sim_state.frozen_dead_zone.get("store_1") is None


@pytest.mark.asyncio
async def test_simulator_inject_visitor():
    """Assert that manual shopper injection spawns a session."""
    initial_visitors = len(sim_state.active_visitors)
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/simulator/control", json={"action": "inject_visitor"})
    
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "Injected customer" in data["message"]
    
    # Active visitors count must increase by 1
    assert len(sim_state.active_visitors) == initial_visitors + 1
