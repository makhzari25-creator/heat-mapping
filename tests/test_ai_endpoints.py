import pytest
from httpx import AsyncClient, ASGITransport
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
from main import app

@pytest.mark.asyncio
async def test_ai_health_score():
    """Assert that GET /ai/health-score returns standard health report schemas."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/ai/health-score?store_id=store_1")
    
    assert r.status_code == 200
    data = r.json()
    assert "score" in data
    assert "classification" in data
    assert "factors" in data
    assert isinstance(data["factors"], list)
    assert "confidence_score" in data
    assert "revenue_opportunity" in data
    assert "ai_insight" in data
    assert data["score"] >= 0 and data["score"] <= 100


@pytest.mark.asyncio
async def test_ai_revenue_opportunities():
    """Assert that GET /ai/revenue-opportunities estimates financial leakage."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/ai/revenue-opportunities?store_id=store_1")
        
    assert r.status_code == 200
    data = r.json()
    assert "total_estimated_leakage" in data
    assert "opportunities" in data
    assert isinstance(data["opportunities"], list)
    assert len(data["opportunities"]) > 0
    assert "confidence_score" in data


@pytest.mark.asyncio
async def test_ai_queue_forecast():
    """Assert that GET /ai/queue-forecast predicts cashier congestions."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/ai/queue-forecast?store_id=store_1")
        
    assert r.status_code == 200
    data = r.json()
    assert "prediction_15m" in data
    assert "prediction_30m" in data
    assert "prediction_60m" in data
    assert "confidence_score" in data


@pytest.mark.asyncio
async def test_ai_copilot_chat():
    """Assert that POST /ai/copilot/chat answers manager queries."""
    payload = {
        "message": "Suggest a staff redeployment schedule based on live active shoppers.",
        "role": "Store Manager"
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/ai/copilot/chat?store_id=store_1", json=payload)
        
    assert r.status_code == 200
    data = r.json()
    assert "answer" in data
    assert "suggestions" in data
    assert len(data["suggestions"]) == 3
    assert "confidence_score" in data


@pytest.mark.asyncio
async def test_ai_benchmark():
    """Assert that GET /ai/benchmark returns multi-store comparisons."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/ai/benchmark?store_id=store_1")
        
    assert r.status_code == 200
    data = r.json()
    assert "date" in data
    assert "benchmarks" in data
    assert len(data["benchmarks"]) == 2
    for b in data["benchmarks"]:
        assert "store_id" in b
        assert "store_name" in b
        assert "total_gmv" in b
        assert "conversion_rate" in b


@pytest.mark.asyncio
async def test_ai_conversion_predictions():
    """Assert that GET /ai/conversion-predictions projects daily run-rates."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/ai/conversion-predictions?store_id=store_1")
        
    assert r.status_code == 200
    data = r.json()
    assert "date" in data
    assert "predictions" in data
    assert len(data["predictions"]) == 4
    for p in data["predictions"]:
        assert "metric_name" in p
        assert "current_value" in p
        assert "predicted_eod_value" in p


@pytest.mark.asyncio
async def test_ai_audit_logs():
    """Assert that GET /ai/audit-logs returns permanent operations logs."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        # First trigger a copilot chat to insert an audit log entry
        payload = {"message": "Test audit log trigger", "role": "VP Operations"}
        await c.post("/ai/copilot/chat?store_id=store_1", json=payload)
        
        # Then fetch logs
        r = await c.get("/ai/audit-logs?limit=5")
        
    assert r.status_code == 200
    data = r.json()
    assert "logs" in data
    assert isinstance(data["logs"], list)
    assert len(data["logs"]) > 0
    assert any(log["user_role"] == "VP Operations" for log in data["logs"])
    assert any(log["action"] == "COPILOT_CHAT_MESSAGE" for log in data["logs"])


@pytest.mark.asyncio
async def test_ai_zone_intelligence():
    """Assert that GET /ai/zone-intelligence/{zone_id} returns detailed zone report."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/ai/zone-intelligence/SKINCARE?store_id=store_1")
        
    assert r.status_code == 200
    data = r.json()
    assert "zone_id" in data
    assert "zone_name" in data
    assert "visitors" in data
    assert "dwell_minutes" in data
    assert "conversion_pct" in data
    assert "revenue" in data
    assert "revenue_opportunity" in data
    assert "ai_insight" in data
    assert "suggested_action" in data
    assert "evidence_package" in data

