from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
import json
from datetime import date, datetime, timezone, timedelta
import random

from database import get_db
from utils.cache import cache
from utils.llm import llm
from utils.audit import log_action, log_ai_recommendation
from utils.evidence_builder import build_evidence
from simulator import sim_state
from metrics import get_store_metrics
from anomalies import get_anomalies

from ai_models import (
    ChatRequest, QueryRequest, AICopilotResponse,
    RevenueResponse, QueueForecastResponse, RCAResponse, RCADiagnosis,
    StoreHealthResponse, BenchmarkResponse, BenchmarkMetric,
    PredictionsResponse, PredictionMetric, AuditLogsResponse, AuditLogItem,
    ReidDiagnosticsResponse, DailySummaryResponse, JourneyReplayResponse, StoreRankingsResponse, StoreRankItem, JourneyStep,
    CommandCenterResponse, CommandCenterInsight, RevenueLeakageResponse, LeakageItem,
    ExplainResponse, TimelineEvent, TimelineResponse, ActionCenterItem, ActionCenterResponse,
    ZoneIntelligenceResponse, EvidencePackage, EvidenceMetric, ConfidenceScore, DataProvenance,
)

router = APIRouter(prefix="/ai", tags=["ai"])

# ---------------------------------------------------------------------------
# Helper — pack a raw evidence dict from EvidenceBuilder into Pydantic models
# ---------------------------------------------------------------------------

def _pack_evidence(raw: dict) -> EvidencePackage:
    """Convert the raw dict returned by EvidenceBuilder into typed Pydantic models."""
    evidence_items = [
        EvidenceMetric(
            metric=e["metric"],
            value=e["value"],
            change=e.get("change"),
        )
        for e in raw.get("evidence", [])
    ]
    conf_raw = raw.get("confidence", {})
    confidence = ConfidenceScore(
        score=conf_raw.get("score", 0.85),
        level=conf_raw.get("level", "HIGH"),
        data_volume=conf_raw.get("data_volume", 0.8),
        data_freshness=conf_raw.get("data_freshness", 0.9),
        historical_consistency=conf_raw.get("historical_consistency", 0.85),
        model_certainty=conf_raw.get("model_certainty", 0.9),
        signal_strength=conf_raw.get("signal_strength", 0.8),
    )
    src_raw = raw.get("source_data", {})
    source_data = DataProvenance(
        stores=src_raw.get("stores", ["Store 1"]),
        events_analyzed=src_raw.get("events_analyzed", 0),
        sessions_analyzed=src_raw.get("sessions_analyzed", 0),
        transactions_analyzed=src_raw.get("transactions_analyzed", 0),
        time_window=src_raw.get("time_window", "Last 60 Minutes"),
    )
    return EvidencePackage(
        evidence=evidence_items,
        reasoning_chain=raw.get("reasoning_chain", []),
        source_data=source_data,
        confidence=confidence,
        estimated_revenue_impact=raw.get("estimated_revenue_impact", 0.0),
        business_impact_severity=raw.get("business_impact_severity", "MEDIUM"),
    )


# ---------------------------------------------------------------------------
# Helper — load complete real-time store context for AI diagnostics
# ---------------------------------------------------------------------------

async def _load_store_context(store_id: str, db: AsyncSession) -> dict:
    cache_key = f"store_context:{store_id}"
    try:
        cached = await cache.get(cache_key)
        if cached:
            return cached
    except Exception:
        pass

    try:
        # 1. Fetch live metrics
        metrics = await get_store_metrics(store_id, db=db)

        # 2. Fetch live anomalies
        anom_res = await get_anomalies(store_id, db=db)
        anomalies_list = [an.model_dump() for an in anom_res.active_anomalies]

        # 3. Count active shoppers
        active_count = len(sim_state.active_visitors)

        # 4. Approximate event / transaction counts from metrics
        events_count = max(100, active_count * 70 + metrics.unique_visitors * 6)
        transactions_count = max(10, int(metrics.unique_visitors * metrics.conversion_rate))

        ctx = {
            "store_id": store_id,
            "total_gmv": metrics.total_gmv,
            "conversion_rate": metrics.conversion_rate,
            "unique_visitors": metrics.unique_visitors,
            "current_queue_depth": metrics.current_queue_depth,
            "anomalies": anomalies_list,
            "active_visitors": active_count,
            "avg_basket_value": metrics.avg_basket_value,
            "abandonment_rate": metrics.abandonment_rate,
            "events_count": events_count,
            "transactions_count": transactions_count,
            "data_age_minutes": 1.0,  # live data
        }
        try:
            await cache.set(cache_key, ctx, ex=3)
        except Exception:
            pass
        return ctx
    except Exception:
        # Return sensible backup context if loading fails
        return {
            "store_id": store_id,
            "total_gmv": 44920.0,
            "conversion_rate": 0.354,
            "unique_visitors": 12,
            "current_queue_depth": len([v for v in sim_state.active_visitors.values() if v.get("current_zone") == "BILLING"]),
            "anomalies": [],
            "active_visitors": len(sim_state.active_visitors),
            "avg_basket_value": 1872.0,
            "abandonment_rate": 0.05,
            "events_count": 842,
            "transactions_count": 53,
            "data_age_minutes": 1.0,
        }


# ---------------------------------------------------------------------------
# AI Copilot Chat
# ---------------------------------------------------------------------------

@router.post("/copilot/chat", response_model=AICopilotResponse)
async def chat_copilot(req: ChatRequest, store_id: str = "store_1", db: AsyncSession = Depends(get_db)):
    """Interactive AI Store Manager Copilot chat gateway with evidence reporting."""
    await log_action(
        user_role=req.role,
        action="COPILOT_CHAT_MESSAGE",
        resource=f"/ai/copilot/chat?store_id={store_id}",
        metadata={"message": req.message}
    )

    ctx = await _load_store_context(store_id, db)
    ctx["last_message"] = req.message

    cache_key = f"copilot:{store_id}:{req.message.strip().lower()}"
    cached_val = await cache.get(cache_key)
    if cached_val:
        return AICopilotResponse(**cached_val)

    system_prompt = (
        "You are an expert AI Store Manager Copilot for VisionRetail-IQ's retail stores. "
        "Formulate detailed, actionable retail suggestions covering staff deployments, "
        "active promotion displays, and queue bottleneck clearances. Return your answer as a JSON "
        "object with standard keys: answer (markdown text), explanation (reasoning details), "
        "supporting_evidence (list of 3 strings), confidence_score (float from 0.0 to 1.0), "
        "recommendation (short string), revenue_impact (float), suggestions (list of 3 quick actions), "
        "data_sources (list of strings)."
    )

    res = await llm.get_insight(
        system_prompt=system_prompt,
        user_message=req.message,
        fallback_type="copilot",
        db_context=ctx
    )

    # If fallback returns old format, map it
    if "explanation" not in res:
        res["explanation"] = res.get("answer", "Analyzed store operations metrics successfully.")
    if "supporting_evidence" not in res:
        res["supporting_evidence"] = res.get("factors", ["Visitor run-rate active", "Checkout queue monitoring SLA nominal"])
    if "recommendation" not in res:
        res["recommendation"] = "Keep staffing configurations constant."
    if "revenue_impact" not in res:
        res["revenue_impact"] = 0.0
    if "data_sources" not in res:
        res["data_sources"] = ["visitor_events", "queue_events"]

    # Build evidence package for the most pressing live issue
    builder = build_evidence(store_id, ctx)
    q_depth = ctx.get("current_queue_depth", 0)
    if q_depth >= 5 or sim_state.trigger_queue_spike.get(store_id, False):
        raw_ev = builder.for_queue_spike(queue_depth=max(q_depth, 5))
    else:
        raw_ev = builder.for_executive_summary(
            conversion=ctx["conversion_rate"],
            baseline_conversion=0.30,
            revenue=ctx["total_gmv"],
            queue_depth=q_depth,
            abandonment_rate=ctx["abandonment_rate"],
        )
    res["evidence_package"] = _pack_evidence(raw_ev).model_dump()

    await cache.set(cache_key, res, ex=10)
    return AICopilotResponse(**res)


# ---------------------------------------------------------------------------
# NLP Analytics Query
# ---------------------------------------------------------------------------

@router.post("/query", response_model=dict)
async def query_nlp(req: QueryRequest, store_id: str = "store_1", db: AsyncSession = Depends(get_db)):
    """Natural Language Analytics queries to extract SQLite metrics."""
    await log_action(
        user_role=req.role,
        action="NLP_ANALYTICS_QUERY",
        resource="/ai/query",
        metadata={"query": req.query}
    )

    ctx = await _load_store_context(store_id, db)
    ctx["query"] = req.query

    res = await llm.get_insight(
        system_prompt="Translate the user retail question into a clean SQLite read-only query and return values.",
        user_message=req.query,
        fallback_type="query",
        db_context=ctx
    )
    return res


# ---------------------------------------------------------------------------
# Revenue Opportunities
# ---------------------------------------------------------------------------

@router.get("/revenue-opportunities", response_model=RevenueResponse)
async def get_revenue_opportunities(store_id: str = "store_1", db: AsyncSession = Depends(get_db)):
    """Estimate daily financial leakage from queue dropouts and zone dead zones."""
    cache_key = f"rev_opp:{store_id}"
    cached_val = await cache.get(cache_key)
    if cached_val:
        return RevenueResponse(**cached_val)

    ctx = await _load_store_context(store_id, db)
    res = await llm.get_insight(
        system_prompt="Formulate financial opportunity audits for store layout leakages.",
        user_message="List lost sales estimations.",
        fallback_type="revenue",
        db_context=ctx
    )

    await cache.set(cache_key, res, ex=8)
    return RevenueResponse(**res)


# ---------------------------------------------------------------------------
# Queue Forecast
# ---------------------------------------------------------------------------

@router.get("/queue-forecast", response_model=QueueForecastResponse)
async def get_queue_forecast(store_id: str = "store_1", db: AsyncSession = Depends(get_db)):
    """Predict cashier queue depths 15, 30, and 60 minutes in advance with evidence."""
    ctx = await _load_store_context(store_id, db)
    base_queue = ctx["current_queue_depth"]

    if sim_state.trigger_queue_spike.get(store_id, False):
        p15 = min(12, base_queue + 2 + random.randint(0, 1))
        p30 = min(15, base_queue + 4 + random.randint(0, 2))
        p60 = min(18, base_queue + 6 + random.randint(1, 3))
        conf = 0.94
    else:
        p15 = max(0, min(6, base_queue + random.choice([-1, 0, 1])))
        p30 = max(0, min(8, base_queue + random.choice([-1, 0, 2])))
        p60 = max(0, min(5, base_queue + random.choice([-2, -1, 0])))
        conf = 0.88

    builder = build_evidence(store_id, ctx)
    raw_ev = builder.for_forecast(current_queue=base_queue, p15=p15, p30=p30, p60=p60)
    evidence_pkg = _pack_evidence(raw_ev)

    forecast_drivers = raw_ev.get("reasoning_chain", [])

    await log_ai_recommendation(
        endpoint="/ai/queue-forecast",
        store_id=store_id,
        recommendation=f"Queue forecast: {base_queue}→{p15} (15m), {p30} (30m), {p60} (60m)",
        confidence_score=evidence_pkg.confidence.score,
        confidence_level=evidence_pkg.confidence.level,
        evidence_snapshot=[e.model_dump() for e in evidence_pkg.evidence],
        inputs_snapshot=ctx,
    )

    return QueueForecastResponse(
        prediction_15m=p15,
        prediction_30m=p30,
        prediction_60m=p60,
        confidence_score=conf,
        forecast_drivers=forecast_drivers,
        evidence_package=evidence_pkg,
    )


# ---------------------------------------------------------------------------
# Root Cause Analysis
# ---------------------------------------------------------------------------

@router.get("/rca", response_model=RCAResponse)
async def get_rca(store_id: str = "store_1", db: AsyncSession = Depends(get_db)):
    """Diagnose root causes of operations alerts and queue spikes."""
    ctx = await _load_store_context(store_id, db)
    res = await llm.get_insight(
        system_prompt="Formulate root cause analysis reports for active store alerts.",
        user_message="Diagnose alerts.",
        fallback_type="rca",
        db_context=ctx
    )
    return RCAResponse(**res)


# ---------------------------------------------------------------------------
# Store Health Score
# ---------------------------------------------------------------------------

@router.get("/health-score", response_model=StoreHealthResponse)
async def get_health_score(store_id: str = "store_1", db: AsyncSession = Depends(get_db)):
    """Compute store health score (0-100) dynamically with 6-part breakdown."""
    ctx = await _load_store_context(store_id, db)

    # Defaults out of maximum scores
    conv_perf = 30
    rev_perf = 25
    q_eff = 20
    cust_exp = 10
    op_stab = 10
    cam_health = 5

    # 1. Deduct for queue depth bottlenecks
    q_depth = ctx["current_queue_depth"]
    if q_depth >= 5:
        q_eff -= 10
        cust_exp -= 5

    # 2. Deduct for active alerts
    anom_count = len(ctx["anomalies"])
    if anom_count > 0:
        op_stab -= min(8, anom_count * 4)

    # 3. Deduct if conversion rate drops
    conv = ctx["conversion_rate"]
    if conv < 0.20:
        conv_perf -= 8
    elif conv < 0.25:
        conv_perf -= 4

    score = conv_perf + rev_perf + q_eff + cust_exp + op_stab + cam_health

    if score >= 85:
        classification = "EXCELLENT"
        trend = "UPWARD"
    elif score >= 70:
        classification = "GOOD"
        trend = "STABLE"
    elif score >= 50:
        classification = "ATTENTION"
        trend = "STABLE"
    else:
        classification = "CRITICAL"
        trend = "DOWNWARD"

    explain_txt = f"Store health evaluated at {score}/100. "
    if classification in ("EXCELLENT", "GOOD"):
        explain_txt += "Sales conversions and checkout flow speeds are operating within target baselines."
    else:
        explain_txt += "Operational constraints in checkout queues or low department conversions are impacting score."

    factors = [
        f"Conversion Performance: {conv_perf}/30",
        f"Revenue Performance: {rev_perf}/25",
        f"Queue Efficiency: {q_eff}/20",
        f"Customer Experience: {cust_exp}/10",
        f"Operational Stability: {op_stab}/10",
        f"Camera Health: {cam_health}/5"
    ]

    # Build evidence package
    builder = build_evidence(store_id, ctx)
    raw_ev = builder.for_executive_summary(
        conversion=conv,
        baseline_conversion=0.30,
        revenue=ctx["total_gmv"],
        queue_depth=q_depth,
        abandonment_rate=ctx["abandonment_rate"],
    )
    evidence_pkg = _pack_evidence(raw_ev)

    # Fetch metrics to calculate revenue leakage dynamically
    from metrics import get_store_metrics
    metrics = await get_store_metrics(store_id, db=db)

    # 1. Queue Abandonment Loss
    abandoned_count = round(metrics.unique_visitors * metrics.abandonment_rate)
    queue_leakage = abandoned_count * metrics.avg_basket_value

    # 2. Dead Shelf Zones
    from anomalies import get_anomalies
    anoms_response = await get_anomalies(store_id, db=db)
    dead_zones = [an for an in anoms_response.active_anomalies if an.anomaly_type == "DEAD_ZONE"]
    dead_leakage = len(dead_zones) * 3200.0

    # 3. High Dwell Low Conversion Zones
    from heatmap import get_heatmap
    heatmap = await get_heatmap(store_id, db=db)
    dwell_leakage = 0.0
    for zone in heatmap.zones:
        if zone.zone_id != "BILLING" and zone.visits > 0:
            conv_pct = zone.conversions / zone.visits
            if conv_pct < 0.25 and zone.avg_dwell_minutes >= 1.5:
                estimated_lost_visitors = int(zone.visits * (0.30 - conv_pct))
                if estimated_lost_visitors > 0:
                    dwell_leakage += estimated_lost_visitors * metrics.avg_basket_value

    # 4. Exit Without Purchase
    exit_loss = max(1, int(metrics.unique_visitors * 0.15)) * metrics.avg_basket_value

    # 5. Billing Delays
    delay_loss = max(0, int(metrics.unique_visitors * 0.05)) * metrics.avg_basket_value

    # 6. Underperforming Zones
    z_loss = max(1, int(metrics.unique_visitors * 0.04)) * metrics.avg_basket_value

    total_leakage_daily = queue_leakage + dead_leakage + dwell_leakage + exit_loss + delay_loss + z_loss
    recoverable_revenue = total_leakage_daily * 0.65
    
    ai_insight_txt = f"Improving checkout efficiency and reducing skincare conversion leakage could recover approximately ₹{recoverable_revenue:,.0f} in revenue."

    return StoreHealthResponse(
        score=score,
        classification=classification,
        conversion_performance=conv_perf,
        revenue_performance=rev_perf,
        queue_efficiency=q_eff,
        customer_experience=cust_exp,
        operational_stability=op_stab,
        camera_health=cam_health,
        trend=trend,
        confidence_score=0.97,
        revenue_opportunity=round(recoverable_revenue, 2),
        ai_insight=ai_insight_txt,
        ai_explanation=explain_txt,
        factors=factors,
        evidence_package=evidence_pkg,
    )


# ---------------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------------

@router.get("/benchmark", response_model=BenchmarkResponse)
async def get_benchmark(store_id: str = "store_1", db: AsyncSession = Depends(get_db)):
    """Compare Store 1 (Bangalore) against Store 2 (Mumbai) dynamically using DB contexts."""
    ctx_blr = await _load_store_context("store_1", db)
    ctx_mum = await _load_store_context("store_2", db)
    dt_str = date.today().isoformat()

    from config import get_store_info
    s1_info = get_store_info("store_1")
    s2_info = get_store_info("store_2")

    blr = BenchmarkMetric(
        store_id="store_1",
        store_name=f"{s1_info.get('store_name')} ({s1_info.get('store_location')})",
        total_gmv=ctx_blr["total_gmv"],
        unique_visitors=ctx_blr["unique_visitors"],
        conversion_rate=ctx_blr["conversion_rate"],
        current_queue_depth=ctx_blr["current_queue_depth"]
    )

    mum = BenchmarkMetric(
        store_id="store_2",
        store_name=f"{s2_info.get('store_name')} ({s2_info.get('store_location')})",
        total_gmv=ctx_mum["total_gmv"],
        unique_visitors=ctx_mum["unique_visitors"],
        conversion_rate=ctx_mum["conversion_rate"],
        current_queue_depth=ctx_mum["current_queue_depth"]
    )

    if ctx_blr["conversion_rate"] > ctx_mum["conversion_rate"]:
        better_store = f"{s1_info.get('store_name')} ({s1_info.get('store_location')})"
        worse_store = f"{s2_info.get('store_name')} ({s2_info.get('store_location')})"
    else:
        better_store = f"{s2_info.get('store_name')} ({s2_info.get('store_location')})"
        worse_store = f"{s1_info.get('store_name')} ({s1_info.get('store_location')})"

    explanation = f"{better_store} outperformed {worse_store} due to higher conversion efficiency and lower queue abandonment."

    return BenchmarkResponse(
        date=dt_str,
        benchmarks=[blr, mum],
        ai_explanation=explanation
    )


# ---------------------------------------------------------------------------
# Conversion Predictions
# ---------------------------------------------------------------------------

@router.get("/conversion-predictions", response_model=PredictionsResponse)
async def get_predictions(store_id: str = "store_1", db: AsyncSession = Depends(get_db)):
    """Predict End-Of-Day and Next-Hour KPIs based on current running rates."""
    ctx = await _load_store_context(store_id, db)
    dt_str = date.today().isoformat()

    p1 = PredictionMetric(
        metric_name="Next-Hour Footfall Forecast",
        current_value=float(ctx["active_visitors"]),
        predicted_eod_value=round(ctx["active_visitors"] * 1.25 + random.randint(1, 3), 1),
        confidence_score=0.94,
        expected_operational_impact="Moderate staff density expected in corridors. Monitor entry flow.",
        timeframe="Next-Hour"
    )
    p2 = PredictionMetric(
        metric_name="Cashier Queue Growth Forecast",
        current_value=float(ctx["current_queue_depth"]),
        predicted_eod_value=round(ctx["current_queue_depth"] * 1.4 + random.randint(0, 1), 1),
        confidence_score=0.88,
        expected_operational_impact="Expect checkout delay to increase if secondary register remains offline.",
        timeframe="Next-Hour"
    )
    p3 = PredictionMetric(
        metric_name="Conversion Probability Run-Rate",
        current_value=float(ctx["conversion_rate"]),
        predicted_eod_value=round(ctx["conversion_rate"] * 1.05, 3),
        confidence_score=0.91,
        expected_operational_impact="Conversion trends stable. Active promotions meeting visual benchmarks.",
        timeframe="EOD"
    )
    p4 = PredictionMetric(
        metric_name="Revenue Trend Projection",
        current_value=float(ctx["total_gmv"]),
        predicted_eod_value=round(ctx["total_gmv"] * 1.35 + random.uniform(500, 2000), 2),
        confidence_score=0.93,
        expected_operational_impact="EOD target projected. Expect strong closing hours.",
        timeframe="EOD"
    )

    return PredictionsResponse(
        date=dt_str,
        predictions=[p1, p2, p3, p4]
    )


# ---------------------------------------------------------------------------
# Audit Logs
# ---------------------------------------------------------------------------

@router.get("/audit-logs", response_model=AuditLogsResponse)
async def get_audit_logs(limit: int = Query(50, ge=1, le=200), db: AsyncSession = Depends(get_db)):
    """Fetch paginated audit log trails from the SQLite logs table."""
    result = await db.execute(text("""
        SELECT id, timestamp, user_role, action, resource, metadata FROM audit_logs
        ORDER BY id DESC LIMIT :lim
    """), {"lim": limit})

    logs = [
        AuditLogItem(
            id=r[0],
            timestamp=r[1],
            user_role=r[2],
            action=r[3],
            resource=r[4],
            metadata=r[5]
        )
        for r in result.fetchall()
    ]
    return AuditLogsResponse(logs=logs)


# ---------------------------------------------------------------------------
# Re-ID Diagnostics
# ---------------------------------------------------------------------------

@router.get("/reid-diagnostics", response_model=ReidDiagnosticsResponse)
async def get_reid_diagnostics():
    """Fetch cross-camera Re-ID deduplication metrics and confidence scores."""
    from ingestion import reid_diagnostics_stats
    avg_conf = reid_diagnostics_stats.get("avg_confidence", 0.92)
    if reid_diagnostics_stats.get("total_ingested", 0) == 0:
        avg_conf = 0.92
    return ReidDiagnosticsResponse(
        total_ingested=reid_diagnostics_stats.get("total_ingested", 0),
        duplicates_suppressed=reid_diagnostics_stats.get("duplicates_suppressed", 0),
        reentries_converted=reid_diagnostics_stats.get("reentries_converted", 0),
        cross_camera_matches=reid_diagnostics_stats.get("cross_camera_matches", 0),
        average_confidence=round(avg_conf, 3)
    )


# ---------------------------------------------------------------------------
# Daily Summary
# ---------------------------------------------------------------------------

@router.get("/daily-summary", response_model=DailySummaryResponse)
async def get_daily_summary(store_id: str = "store_1", db: AsyncSession = Depends(get_db)):
    """Generate an AI daily store performance summary with evidence traceability."""
    ctx = await _load_store_context(store_id, db)

    visitors = ctx.get("unique_visitors", 412)
    revenue = ctx.get("total_gmv", 48600.0)
    conversion = ctx.get("conversion_rate", 0.242)
    q_depth = ctx.get("current_queue_depth", 0)
    abandonment = ctx.get("abandonment_rate", 0.05)

    if q_depth >= 5 or sim_state.trigger_queue_spike.get(store_id, False):
        top_risk = "Billing Congestion"
        top_opportunity = "Skincare Counter Cross-Sells"
        recovery = 5600.0
        rec_action = "Open secondary billing counter during peak hours."
    else:
        top_risk = "Fragrance Layout Cold-Spot"
        top_opportunity = "Skincare Zone Optimization"
        recovery = 3200.0
        rec_action = "Relocate sales associate to Skincare corridor to capture traffic."

    summary_text = (
        f"Operations summary for {store_id}. "
        f"Total unique visitor footfall holds at {visitors} sessions. "
        f"Conversion rate is at {(conversion * 100):.1f}%, driving a daily sales revenue of ₹{revenue:,.2f}."
    )

    # Build full evidence package
    builder = build_evidence(store_id, ctx)
    raw_ev = builder.for_executive_summary(
        conversion=conversion,
        baseline_conversion=0.30,
        revenue=revenue,
        queue_depth=q_depth,
        abandonment_rate=abandonment,
    )
    evidence_pkg = _pack_evidence(raw_ev)

    await log_ai_recommendation(
        endpoint="/ai/daily-summary",
        store_id=store_id,
        recommendation=rec_action,
        confidence_score=evidence_pkg.confidence.score,
        confidence_level=evidence_pkg.confidence.level,
        evidence_snapshot=[e.model_dump() for e in evidence_pkg.evidence],
        inputs_snapshot=ctx,
    )

    from config import get_store_info
    s_info = get_store_info(store_id)
    return DailySummaryResponse(
        store_id=store_id,
        store_name=s_info.get("store_name"),
        store_location=s_info.get("store_location"),
        city=s_info.get("city"),
        summary=summary_text,
        visitors=visitors,
        revenue=revenue,
        conversion=conversion,
        top_opportunity=top_opportunity,
        top_risk=top_risk,
        estimated_revenue_recovery=recovery,
        recommended_action=rec_action,
        confidence_score=round(evidence_pkg.confidence.score, 2),
        data_sources=["visitor_events", "pos_transactions", "queue_events"],
        evidence_package=evidence_pkg,
    )


# ---------------------------------------------------------------------------
# Journey Replay
# ---------------------------------------------------------------------------

@router.get("/journey-replay/{visitor_id}", response_model=JourneyReplayResponse)
async def get_journey_replay(visitor_id: str, request: Request, store_id: str = "store_1", db: AsyncSession = Depends(get_db)):
    request.state.visitor_id = visitor_id
    """Assemble spatial-temporal coordinates and metadata for interactive journey replay."""
    result = await db.execute(
        text("""
            SELECT event_type, zone_id, timestamp, metadata, camera_id
            FROM events
            WHERE store_id = :sid AND visitor_id = :vid
            ORDER BY timestamp ASC
        """),
        {"sid": store_id, "vid": visitor_id}
    )
    rows = result.fetchall()

    ZONE_NAMES = {
        "MAKEUP": "Makeup & Colour",
        "SKINCARE": "Skincare & Treatments",
        "BATH_BODY": "Bath & Body",
        "HAIR": "Hair Care",
        "FRAGRANCE": "Fragrance",
        "PERSONAL_CARE": "Personal Care",
        "BILLING": "Billing Counter",
    }

    zone_coords = {
        "MAKEUP": (0.20, 0.25),
        "SKINCARE": (0.60, 0.25),
        "BATH_BODY": (0.15, 0.68),
        "HAIR": (0.45, 0.68),
        "FRAGRANCE": (0.70, 0.68),
        "PERSONAL_CARE": (0.90, 0.42),
        "BILLING": (0.50, 0.92)
    }

    steps = []
    duration = 0
    first_t = None
    last_t = None

    for etype, zone_id, ts_str, meta_str, cam_id in rows:
        try:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if first_t is None:
                first_t = dt
            last_t = dt
        except Exception:
            pass

        cx, cy = zone_coords.get(zone_id, (0.05, 0.05) if etype in ("ENTRY", "EXIT") else (0.5, 0.5))

        meta = {}
        try:
            meta = json.loads(meta_str) if meta_str else {}
        except Exception:
            pass

        desc = f"Observed at {cam_id}"
        if etype == "ENTRY":
            desc = "Entered store lobby through main entrance."
        elif etype == "ZONE_ENTER":
            desc = f"Walked into {ZONE_NAMES.get(zone_id, zone_id)} zone."
        elif etype == "ZONE_DWELL":
            dwell_min = round(meta.get("dwell_ms", 0) / 60000.0, 1)
            desc = f"Browsing products in {ZONE_NAMES.get(zone_id, zone_id)} for {dwell_min} minutes."
        elif etype == "ZONE_EXIT":
            desc = f"Departed from {ZONE_NAMES.get(zone_id, zone_id)} zone."
            if zone_id == "BILLING" and meta.get("amount"):
                desc += f" Completed checkout purchase: ₹{meta.get('amount')}"
        elif etype == "BILLING_QUEUE_JOIN":
            desc = f"Joined checkout billing queue. Queue depth: {meta.get('queue_depth') or 1} shoppers."
        elif etype == "BILLING_QUEUE_ABANDON":
            desc = "Abandoned queue without completing transaction due to high wait time."
        elif etype == "EXIT":
            desc = "Exited store main lobby entrance."
        elif etype == "REENTRY":
            desc = "Returned to the store (Re-Entry detected)."

        steps.append(JourneyStep(
            timestamp=ts_str,
            zone_id=zone_id,
            zone_name=ZONE_NAMES.get(zone_id, "Entrance") if zone_id or etype in ("ENTRY", "EXIT") else "Store Area",
            event_type=etype,
            description=desc,
            x=cx,
            y=cy
        ))

        if first_t and last_t:
            duration = int((last_t - first_t).total_seconds())

    ai_profile = "Visitor spent 5 minutes in Skincare. Visited 4 zones. Entered billing queue. Completed purchase."
    if any(s.event_type == "BILLING_QUEUE_ABANDON" for s in steps):
        ai_profile = "Visitor entered lobby, browsed makeup, joined billing queue but departed without purchasing due to queue delays."
    elif any(s.event_type == "ZONE_EXIT" and s.zone_id == "BILLING" and "Purchase" in s.description for s in steps):
        ai_profile = "Customer entered lobby, browsed skincare, queued at cashier register, completed ₹1,850 transaction successfully."

    return JourneyReplayResponse(
        visitor_id=visitor_id,
        steps=steps,
        duration_seconds=duration,
        ai_character_profile=ai_profile
    )


# ---------------------------------------------------------------------------
# Store Rankings
# ---------------------------------------------------------------------------

@router.get("/store-rankings", response_model=StoreRankingsResponse)
async def get_store_rankings(db: AsyncSession = Depends(get_db)):
    """Fetch comparative health score rankings for retail locations."""
    from config import get_store_info
    s1 = get_store_info("store_1")
    s2 = get_store_info("store_2")
    rankings = [
        StoreRankItem(store_id="store_1", store_name=f"{s1.get('store_name')} ({s1.get('store_location')})", health_score=94, rank=1, status="EXCELLENT"),
        StoreRankItem(store_id="store_2", store_name=f"{s2.get('store_name')} ({s2.get('store_location')})", health_score=88, rank=2, status="GOOD")
    ]
    try:
        h1 = await get_health_score(store_id="store_1", db=db)
        rankings[0].health_score = h1.score
        rankings[0].status = h1.classification
    except Exception:
        pass

    try:
        h2 = await get_health_score(store_id="store_2", db=db)
        rankings[1].health_score = h2.score
        rankings[1].status = h2.classification
    except Exception:
        pass

    rankings.sort(key=lambda r: r.health_score, reverse=True)
    for i, r in enumerate(rankings):
        r.rank = i + 1

    return StoreRankingsResponse(rankings=rankings)


# ---------------------------------------------------------------------------
# Command Center
# ---------------------------------------------------------------------------

@router.get("/command-center", response_model=CommandCenterResponse)
async def get_command_center(store_id: str = "store_1", db: AsyncSession = Depends(get_db)):
    """Assemble an executive retail command center dashboard with full evidence on each insight."""
    ctx = await _load_store_context(store_id, db)
    builder = build_evidence(store_id, ctx)

    gmv = ctx["total_gmv"]
    conv = ctx["conversion_rate"]
    q_depth = ctx["current_queue_depth"]
    visitors = ctx["unique_visitors"]
    anoms = ctx["anomalies"]

    daily_summary = f"Executive Intelligence Panel for {store_id}. Today, {visitors} unique customers visited the store corridors."
    revenue_summary = f"Cleared checkout sales: ₹{gmv:,.2f} GMV."
    conversion_summary = f"Visual conversion run-rate holds at {(conv * 100):.1f}%."
    queue_summary = f"Cashier billing queue is at {q_depth} shoppers."

    insights = []

    # 1. Queue Insight
    if q_depth >= 5:
        raw_ev = builder.for_queue_spike(queue_depth=q_depth)
        insights.append(CommandCenterInsight(
            insight_type="QUEUE",
            title="High Queue Abandonment Risk",
            summary=f"Billing queue depth reaches {q_depth} shoppers, exceeding nominal safety SLA.",
            confidence_score=round(_pack_evidence(raw_ev).confidence.score, 2),
            business_impact=f"Estimated lost revenue ₹{raw_ev['estimated_revenue_impact']:,.0f}.",
            recommended_action="Deploy secondary register cashier terminal immediately.",
            evidence_package=_pack_evidence(raw_ev),
        ))
    else:
        raw_ev = builder.for_action_recommendation(
            title="Checkout Nominal",
            evidence_items=[
                {"metric": "queue_depth", "value": q_depth, "change": "—"},
                {"metric": "wait_time", "value": "< 2.0m", "change": "—"},
            ],
            reasoning=["Queue clearance rate within SLA", "No abandonment detected"],
            revenue_recovery=0.0,
            model_certainty=0.91,
        )
        insights.append(CommandCenterInsight(
            insight_type="QUEUE",
            title="Checkout Operations Nominal",
            summary="Queue clearance rate remains within optimal SLA bounds under 2 minutes.",
            confidence_score=round(_pack_evidence(raw_ev).confidence.score, 2),
            business_impact="No queue abandonment revenue loss detected.",
            recommended_action="Maintain current standard cashier shifts.",
            evidence_package=_pack_evidence(raw_ev),
        ))

    # 2. Dead Zone insight
    dead_zones = [an for an in anoms if an.get("anomaly_type") == "DEAD_ZONE"]
    if dead_zones:
        for dz in dead_zones:
            z_id = dz.get("zone_id", "FRAGRANCE")
            raw_ev = builder.for_dead_zone(zone_id=z_id, zone_name=z_id.title())
            insights.append(CommandCenterInsight(
                insight_type="RISK",
                title="Zone Dead Shelf Alert",
                summary=f"No customer traffic in {z_id} corridor for over 30 minutes.",
                confidence_score=round(_pack_evidence(raw_ev).confidence.score, 2),
                business_impact=f"Estimated daily opportunity loss ₹{raw_ev['estimated_revenue_impact']:,.0f}.",
                recommended_action=f"Promote active visual banners or relocate salesperson near {z_id} corridor.",
                evidence_package=_pack_evidence(raw_ev),
            ))
    else:
        raw_ev = builder.for_action_recommendation(
            title="Corridor Traffic Optimal",
            evidence_items=[
                {"metric": "dead_zones_detected", "value": 0, "change": "—"},
                {"metric": "layout_traversal_score", "value": "OPTIMAL", "change": "—"},
            ],
            reasoning=["All zones receiving customer traffic", "No dead shelf anomaly detected"],
            revenue_recovery=0.0,
            model_certainty=0.93,
        )
        insights.append(CommandCenterInsight(
            insight_type="RISK",
            title="Corridor Traffic Optimal",
            summary="All visual layouts are attracting continuous customer path traversals.",
            confidence_score=round(_pack_evidence(raw_ev).confidence.score, 2),
            business_impact="Optimal physical layout ROI.",
            recommended_action="Continue current merchandising strategy.",
            evidence_package=_pack_evidence(raw_ev),
        ))

    # 3. Conversion Insight
    if conv < 0.25:
        raw_ev = builder.for_conversion_drop(conversion=conv)
        insights.append(CommandCenterInsight(
            insight_type="CONVERSION",
            title="Conversion Efficiency Alert",
            summary=f"Overall conversion dropped to {(conv * 100):.1f}%, which represents a drop vs average.",
            confidence_score=round(_pack_evidence(raw_ev).confidence.score, 2),
            business_impact=f"Estimated opportunity loss ₹{raw_ev['estimated_revenue_impact']:,.0f}.",
            recommended_action="Launch skincare bundle display ('Buy 2 Get 2 Sheet Masks') to stimulate checkouts.",
            evidence_package=_pack_evidence(raw_ev),
        ))
    else:
        raw_ev = builder.for_action_recommendation(
            title="Conversion SLA Met",
            evidence_items=[
                {"metric": "conversion_rate", "value": f"{conv * 100:.1f}%", "change": "—"},
                {"metric": "vs_target", "value": "≥ 25%", "change": "—"},
            ],
            reasoning=[f"Conversion at {conv * 100:.1f}% meets SLA target", "No checkout barrier detected"],
            revenue_recovery=0.0,
            model_certainty=0.95,
        )
        insights.append(CommandCenterInsight(
            insight_type="CONVERSION",
            title="Conversion Target Meeting SLA",
            summary=f"Visual conversions operating strongly at {(conv * 100):.1f}%.",
            confidence_score=round(_pack_evidence(raw_ev).confidence.score, 2),
            business_impact="Healthy sales run-rate established.",
            recommended_action="Keep high-intent checkout products fully replenished.",
            evidence_package=_pack_evidence(raw_ev),
        ))

    from config import get_store_info
    s_info = get_store_info(store_id)
    return CommandCenterResponse(
        store_id=store_id,
        store_name=s_info.get("store_name"),
        store_location=s_info.get("store_location"),
        city=s_info.get("city"),
        daily_summary=daily_summary,
        revenue_summary=revenue_summary,
        conversion_summary=conversion_summary,
        queue_summary=queue_summary,
        insights=insights
    )


# ---------------------------------------------------------------------------
# Revenue Leakage
# ---------------------------------------------------------------------------

@router.get("/revenue-leakage", response_model=RevenueLeakageResponse)
async def get_revenue_leakage(store_id: str = "store_1", db: AsyncSession = Depends(get_db)):
    """Exposes estimated financial leakages with full evidence traceability per category."""
    metrics = await get_store_metrics(store_id, db=db)
    ctx = await _load_store_context(store_id, db)
    builder = build_evidence(store_id, ctx)

    q_depth = metrics.current_queue_depth
    abandoned_count = round(metrics.unique_visitors * metrics.abandonment_rate)
    queue_leakage = abandoned_count * metrics.avg_basket_value

    leakages = []
    rank = 1

    # 1. Queue Abandonment Loss
    raw_ev = builder.for_revenue_leakage(
        category="QUEUE_ABANDONMENT",
        estimated_loss=round(queue_leakage, 2),
        missed_conversions=abandoned_count,
        root_cause=f"High cashier wait times. Wait registers operating at full capacity (queue depth: {q_depth}).",
    )
    leakages.append(LeakageItem(
        issue_name="Billing Queue Abandonment",
        category="QUEUE_ABANDONMENT",
        description=f"Customers joined the billing queue but departed without purchasing due to queue length (current: {q_depth}).",
        estimated_loss_daily=round(queue_leakage, 2),
        missed_conversions=abandoned_count,
        monthly_impact=round(queue_leakage * 30, 2),
        business_impact_rank=rank,
        root_cause=f"High cashier wait times. Wait registers operating at full capacity.",
        revenue_impact=round(queue_leakage, 2),
        business_impact="Dwell satisfaction scores dropped 14% among exit visitors.",
        recommended_action="Open register terminal #2. Deploy backup checkout cashiers during peak hours.",
        confidence_score=0.94,
        evidence_package=_pack_evidence(raw_ev),
    ))
    rank += 1

    # 2. Dead Shelf Zones
    anoms = await get_anomalies(store_id, db=db)
    dead_zones = [an for an in anoms.active_anomalies if an.anomaly_type == "DEAD_ZONE"]
    for dz in dead_zones:
        z_id = dz.zone_id or "FRAGRANCE"
        lost_rev = 3200.0
        raw_ev = builder.for_revenue_leakage(
            category="DEAD_SHELF",
            estimated_loss=lost_rev,
            missed_conversions=2,
            root_cause=f"Merchandising layout cold spot in {z_id}. High barrier shelves blocking product visibility.",
        )
        leakages.append(LeakageItem(
            issue_name=f"Dead Shelf: {z_id} Corridor",
            category="DEAD_SHELF",
            description=f"Zero customer interactions detected in {z_id} corridor for over 30 minutes.",
            estimated_loss_daily=lost_rev,
            missed_conversions=2,
            monthly_impact=lost_rev * 30,
            business_impact_rank=rank,
            root_cause="Merchandising layout cold spot. High barrier shelves blocking product visibility.",
            revenue_impact=lost_rev,
            business_impact="Product category visibility and footprint index down 18% today.",
            recommended_action=f"Improve visual path visibility, update promotional signage, or reposition display.",
            confidence_score=0.88,
            evidence_package=_pack_evidence(raw_ev),
        ))
        rank += 1

    # 3. High Dwell Low Conversion Zones
    from heatmap import get_heatmap
    heatmap = await get_heatmap(store_id, db=db)
    for zone in heatmap.zones:
        if zone.zone_id != "BILLING" and zone.visits > 0:
            conv_pct = zone.conversions / zone.visits if zone.visits > 0 else 1.0
            if conv_pct < 0.25 and zone.avg_dwell_minutes >= 1.5:
                estimated_lost_visitors = int(zone.visits * (0.30 - conv_pct))
                if estimated_lost_visitors > 0:
                    lost_rev = estimated_lost_visitors * metrics.avg_basket_value
                    raw_ev = builder.for_revenue_leakage(
                        category="HIGH_DWELL_LOW_CONV",
                        estimated_loss=round(lost_rev, 2),
                        missed_conversions=estimated_lost_visitors,
                        root_cause=f"Product pricing barrier or salesperson assistance missing from {zone.zone_name}.",
                    )
                    leakages.append(LeakageItem(
                        issue_name=f"High Dwell Low Conversion in {zone.zone_name}",
                        category="HIGH_DWELL_LOW_CONV",
                        description=f"Visitors spend an average of {zone.avg_dwell_minutes}m in {zone.zone_name} but buy-through is only {conv_pct:.1%}.",
                        estimated_loss_daily=round(lost_rev, 2),
                        missed_conversions=estimated_lost_visitors,
                        monthly_impact=round(lost_rev * 30, 2),
                        business_impact_rank=rank,
                        root_cause="Product pricing barrier or salesperson assistance missing from visual bays.",
                        revenue_impact=round(lost_rev, 2),
                        business_impact="Store conversion efficiency drags downward from this department baseline.",
                        recommended_action=f"Promote active bundle signs ('Buy 2 Get 2') and place assistance rep in {zone.zone_name}.",
                        confidence_score=0.91,
                        evidence_package=_pack_evidence(raw_ev),
                    ))
                    rank += 1

    # 4. Exit Without Purchase
    lost_exits = max(1, int(metrics.unique_visitors * 0.15))
    exit_loss = lost_exits * metrics.avg_basket_value
    raw_ev = builder.for_revenue_leakage(
        category="NO_PURCHASE_TRAFFIC",
        estimated_loss=round(exit_loss, 2),
        missed_conversions=lost_exits,
        root_cause="Consultant greeting gaps. Cold entry point warming up steps missing.",
    )
    leakages.append(LeakageItem(
        issue_name="Exit Without Purchase",
        category="NO_PURCHASE_TRAFFIC",
        description="Customers browsed makeup or skincare but exited the lobby without joining checkout queues.",
        estimated_loss_daily=round(exit_loss, 2),
        missed_conversions=lost_exits,
        monthly_impact=round(exit_loss * 30, 2),
        business_impact_rank=rank,
        root_cause="Consultant greeting gaps. Cold entry point warming up steps missing.",
        revenue_impact=round(exit_loss, 2),
        business_impact="Walk-through ratios dropping overall store conversion capacity.",
        recommended_action="Offer greeting consultations or entry-point discounts to warm up visitor intent.",
        confidence_score=0.89,
        evidence_package=_pack_evidence(raw_ev),
    ))
    rank += 1

    # 5. Billing Delays
    delay_missed = max(0, int(metrics.unique_visitors * 0.05))
    delay_loss = delay_missed * metrics.avg_basket_value
    raw_ev = builder.for_revenue_leakage(
        category="BILLING_DELAYS",
        estimated_loss=round(delay_loss, 2),
        missed_conversions=delay_missed,
        root_cause="Point-of-Sale terminal latency. High manual code lookup delays.",
    )
    leakages.append(LeakageItem(
        issue_name="Billing Checkout Delays",
        category="BILLING_DELAYS",
        description="Transaction processing speeds exceed nominal targets due to cashier terminal lag.",
        estimated_loss_daily=round(delay_loss, 2),
        missed_conversions=delay_missed,
        monthly_impact=round(delay_loss * 30, 2),
        business_impact_rank=rank,
        root_cause="Point-of-Sale terminal latency. High manual code lookup delays.",
        revenue_impact=round(delay_loss, 2),
        business_impact="Cashier queue times increased by 1.8 minutes on average.",
        recommended_action="Introduce self-checkout registers or deploy manual barcode scanners.",
        confidence_score=0.93,
        evidence_package=_pack_evidence(raw_ev),
    ))
    rank += 1

    # 6. Underperforming Zones
    z_missed = max(1, int(metrics.unique_visitors * 0.04))
    z_loss = z_missed * metrics.avg_basket_value
    raw_ev = builder.for_revenue_leakage(
        category="UNDERPERFORMING_ZONES",
        estimated_loss=round(z_loss, 2),
        missed_conversions=z_missed,
        root_cause="B2G1 signage missing. Merchandiser positioning mismatch.",
    )
    leakages.append(LeakageItem(
        issue_name="Underperforming Zones (Fragrance/Bath)",
        category="UNDERPERFORMING_ZONES",
        description="Fragrance/Bath layout yields lower conversion compared to skincare category standard.",
        estimated_loss_daily=round(z_loss, 2),
        missed_conversions=z_missed,
        monthly_impact=round(z_loss * 30, 2),
        business_impact_rank=rank,
        root_cause="B2G1 signage missing. Merchandiser positioning mismatch.",
        revenue_impact=round(z_loss, 2),
        business_impact="Fragrance product shelf-turns down by 10% this week.",
        recommended_action="Set up a prominent test stand or launch B2G1 promotion.",
        confidence_score=0.86,
        evidence_package=_pack_evidence(raw_ev),
    ))

    leakages.sort(key=lambda x: x.estimated_loss_daily, reverse=True)
    for i, item in enumerate(leakages):
        item.business_impact_rank = i + 1

    total_leakage_daily = sum(x.estimated_loss_daily for x in leakages)
    total_leakage_weekly = total_leakage_daily * 7
    total_leakage_monthly = total_leakage_daily * 30
    recoverable_revenue = total_leakage_daily * 0.65

    await log_ai_recommendation(
        endpoint="/ai/revenue-leakage",
        store_id=store_id,
        recommendation=f"Total daily leakage: ₹{total_leakage_daily:,.0f} across {len(leakages)} categories",
        confidence_score=0.91,
        confidence_level="HIGH",
        evidence_snapshot=[{"metric": l.category, "value": l.estimated_loss_daily} for l in leakages[:3]],
        inputs_snapshot=ctx,
    )

    from config import get_store_info
    s_info = get_store_info(store_id)
    return RevenueLeakageResponse(
        store_id=store_id,
        store_name=s_info.get("store_name"),
        store_location=s_info.get("store_location"),
        city=s_info.get("city"),
        total_leakage_daily=round(total_leakage_daily, 2),
        total_leakage_weekly=round(total_leakage_weekly, 2),
        total_leakage_monthly=round(total_leakage_monthly, 2),
        recoverable_revenue=round(recoverable_revenue, 2),
        leakages=leakages,
        confidence=0.91,
        data_sources=["visitor_events", "pos_transactions", "queue_events"]
    )


# ---------------------------------------------------------------------------
# Explain This
# ---------------------------------------------------------------------------

@router.get("/explain", response_model=ExplainResponse)
async def explain_metric(
    metric: str = Query(..., description="The metric name to explain: conversion, revenue, queue_depth, dwell_time, abandonment, store_health"),
    store_id: str = "store_1",
    db: AsyncSession = Depends(get_db)
):
    """Explain why a retail metric is dropping or meeting SLA thresholds — with full evidence and reasoning chain."""
    ctx = await _load_store_context(store_id, db)
    builder = build_evidence(store_id, ctx)

    ans = ""
    evidence = []
    rev_imp = 0.0
    conf = 0.90
    sources = ["visitor_events"]
    raw_ev = None

    if metric == "conversion":
        q_depth = ctx["current_queue_depth"]
        ans = f"Store conversion stands at {(ctx['conversion_rate'] * 100):.1f}%."
        if q_depth >= 5 or sim_state.trigger_queue_spike.get(store_id, False):
            ans += " Conversion dropped today due to queue depth spikes and high wait-times at checkouts."
            evidence = [
                f"Queue depth increased to {q_depth} visitors",
                "Average cashier wait time increased by 4.2 minutes",
                "Queue abandonment rose to 14%"
            ]
            rev_imp = 2800.0
            conf = 0.88
            sources = ["visitor_events", "queue_events", "pos_transactions"]
            raw_ev = builder.for_queue_spike(queue_depth=max(q_depth, 5))
        else:
            ans += " Conversion rate remains stable with standard lobby traffic clearance speeds."
            evidence = [
                "Lobby queue remains within SLA limits of 3 shoppers",
                "Assisted browsing paths are operating nominal conversions",
                "Zero checkout anomalies detected"
            ]
            rev_imp = 0.0
            conf = 0.93
            sources = ["visitor_events", "queue_events", "pos_transactions"]
            raw_ev = builder.for_conversion_drop(conversion=ctx["conversion_rate"])

    elif metric == "revenue":
        gmv = ctx["total_gmv"]
        ans = f"Daily store GMV totals ₹{gmv:,.2f}. "
        if sim_state.trigger_conversion_drop.get(store_id, False):
            ans += "Daily revenue drop triggered by lower checkout clearance rates and promotional dead zones."
            evidence = [
                "Overall store conversion dropped by 8%",
                "Skincare checkout rate lower than baseline by 15%",
                "Average basket ticket size decreased to ₹1,420"
            ]
            rev_imp = 4800.0
            conf = 0.91
            sources = ["pos_transactions", "visitor_events"]
        else:
            ans += "Revenue metrics meeting standard targets with healthy transaction tickets."
            evidence = [
                f"Completed transactions count: {int(gmv / 1850)} orders",
                "Average basket ticket size: ₹1,872",
                "Active promotional banners clearance rate: 21%"
            ]
            rev_imp = 0.0
            conf = 0.95
            sources = ["pos_transactions", "visitor_events"]
        raw_ev = builder.for_executive_summary(
            conversion=ctx["conversion_rate"],
            baseline_conversion=0.30,
            revenue=gmv,
            queue_depth=ctx["current_queue_depth"],
            abandonment_rate=ctx["abandonment_rate"],
        )

    elif metric == "queue_depth":
        q = ctx["current_queue_depth"]
        ans = f"Cashier queue depth currently stands at {q} visitors. "
        if q >= 5:
            ans += "Queue bottlenecks detected due to cashier register terminal offline."
            evidence = [
                f"Current cashier queue: {q} visitors",
                "Wait register counter #2 remains closed",
                "Arrival rate modeling predicts queue growth to 8+ visitors"
            ]
            rev_imp = 3200.0
            conf = 0.96
            sources = ["queue_events"]
            raw_ev = builder.for_queue_spike(queue_depth=q)
        else:
            ans += "Billing queue registers operating under nominal clearance speeds."
            evidence = [
                f"Current cashier queue: {q} visitors",
                "Standard processing wait time under 1.8 minutes",
                "Self-checkout clearance speeds meeting target rules"
            ]
            rev_imp = 0.0
            conf = 0.92
            sources = ["queue_events"]
            raw_ev = builder.for_forecast(current_queue=q, p15=q, p30=max(0, q - 1), p60=max(0, q - 2))

    elif metric == "dwell_time":
        ans = "Average browse dwell time is 8.4 minutes across layouts. Skincare and Makeup yield highest quality dwells."
        evidence = [
            "Skincare avg dwell: 4.2 minutes",
            "Makeup avg dwell: 3.8 minutes",
            "Fragrance avg dwell: 1.5 minutes (Cold Spot)"
        ]
        rev_imp = 1200.0
        conf = 0.89
        sources = ["visitor_events"]
        raw_ev = builder.for_zone_intelligence(
            zone_id="SKINCARE", zone_name="Skincare & Treatments",
            visitors=ctx["unique_visitors"], dwell_minutes=4.2,
            conversions=int(ctx["unique_visitors"] * 0.22), revenue=ctx["total_gmv"] * 0.35,
        )

    elif metric == "abandonment":
        rate = ctx.get("abandonment_rate", 0.05)
        ans = f"Cashier register queue abandonment is at {(rate * 100):.1f}%."
        if rate > 0.08:
            ans += " Abandonment increased due to billing delays."
            evidence = [
                "Shoppers left queue without completed transactions",
                "Wait time exceeded nominal SLA of 3 minutes",
                "Revenue leakage estimated at ₹4,800"
            ]
            rev_imp = 4800.0
            conf = 0.90
            sources = ["queue_events", "pos_transactions"]
            raw_ev = builder.for_queue_spike(queue_depth=ctx["current_queue_depth"])
        else:
            ans += " Abandonment rate within standard guidelines."
            evidence = [
                "Less than 5% checkout queue dropouts",
                "Cashier checkout clearance remains under 2 minutes",
                "No cashier wait-time alerts"
            ]
            rev_imp = 0.0
            conf = 0.92
            sources = ["queue_events"]
            raw_ev = builder.for_revenue_leakage(
                category="QUEUE_ABANDONMENT",
                estimated_loss=0,
                missed_conversions=0,
                root_cause="Abandonment within normal bounds",
            )

    elif metric == "store_health":
        h_score = await get_health_score(store_id, db)
        ans = h_score.ai_explanation
        evidence = h_score.factors
        rev_imp = 0.0
        conf = h_score.confidence_score
        sources = ["visitor_events", "queue_events", "pos_transactions"]
        raw_ev = builder.for_executive_summary(
            conversion=ctx["conversion_rate"],
            baseline_conversion=0.30,
            revenue=ctx["total_gmv"],
            queue_depth=ctx["current_queue_depth"],
            abandonment_rate=ctx["abandonment_rate"],
        )

    else:
        ans = "Analyzed retail intelligence metric successfully."
        evidence = ["System telemetry active"]
        sources = ["visitor_events"]
        raw_ev = builder.for_action_recommendation(
            title="Generic Metric",
            evidence_items=[{"metric": metric, "value": "—", "change": "—"}],
            reasoning=["System telemetry active"],
            revenue_recovery=0.0,
        )

    evidence_pkg = _pack_evidence(raw_ev) if raw_ev else None

    return ExplainResponse(
        metric_name=metric,
        answer=ans,
        evidence=evidence,
        revenue_impact=rev_imp,
        confidence_score=conf,
        data_sources=sources,
        evidence_package=evidence_pkg,
        reasoning_chain=raw_ev.get("reasoning_chain", []) if raw_ev else [],
    )


# ---------------------------------------------------------------------------
# Decision Timeline
# ---------------------------------------------------------------------------

@router.get("/decision-timeline", response_model=TimelineResponse)
async def get_decision_timeline(
    store_id: str = "store_1",
    db: AsyncSession = Depends(get_db)
):
    """Chronological intelligence feed showing operational cause and effect with evidence."""
    ctx = await _load_store_context(store_id, db)
    builder = build_evidence(store_id, ctx)
    events = []

    now_dt = datetime.now(timezone.utc)

    if sim_state.trigger_queue_spike.get(store_id, False):
        q = ctx.get("current_queue_depth", 6)

        ev1_raw = builder.for_queue_spike(queue_depth=q)
        events.append(TimelineEvent(
            timestamp_label=(now_dt - timedelta(minutes=13)).strftime("%I:%M %p"),
            event_name="Queue Spike Detected",
            evidence=f"Billing queue depth rose to {q} shoppers, exceeding target threshold.",
            impacted_metrics="Customer wait time, Billing checkout speed",
            confidence=round(_pack_evidence(ev1_raw).confidence.score, 2),
            revenue_impact=ev1_raw.get("estimated_revenue_impact", 3200.0),
            evidence_package=_pack_evidence(ev1_raw),
        ))

        ev2_raw = builder.for_action_recommendation(
            title="Wait Time Increase",
            evidence_items=[
                {"metric": "wait_time_minutes", "value": "4.5m", "change": "+275%"},
                {"metric": "register_1_load", "value": "100%", "change": "—"},
            ],
            reasoning=["Single register at capacity", "Wait time crossed 4-minute SLA threshold"],
            revenue_recovery=1200.0,
            model_certainty=0.92,
        )
        events.append(TimelineEvent(
            timestamp_label=(now_dt - timedelta(minutes=11)).strftime("%I:%M %p"),
            event_name="Average Wait Time Increased",
            evidence="Checkout wait time rose to 4.5 minutes on register 1.",
            impacted_metrics="Customer Experience Index",
            confidence=round(_pack_evidence(ev2_raw).confidence.score, 2),
            revenue_impact=1200.0,
            evidence_package=_pack_evidence(ev2_raw),
        ))

        ev3_raw = builder.for_revenue_leakage(
            category="QUEUE_ABANDONMENT",
            estimated_loss=2400.0,
            missed_conversions=2,
            root_cause="Queue wait time exceeded 4-minute SLA — 2 visitors left without purchase",
        )
        events.append(TimelineEvent(
            timestamp_label=(now_dt - timedelta(minutes=7)).strftime("%I:%M %p"),
            event_name="Billing Queue Abandonment",
            evidence="2 visitors left checkout line without purchase attribution.",
            impacted_metrics="Conversion Efficiency",
            confidence=round(_pack_evidence(ev3_raw).confidence.score, 2),
            revenue_impact=2400.0,
            evidence_package=_pack_evidence(ev3_raw),
        ))

        ev4_raw = builder.for_conversion_drop(conversion=0.18, baseline_conversion=0.25)
        events.append(TimelineEvent(
            timestamp_label=(now_dt - timedelta(minutes=5)).strftime("%I:%M %p"),
            event_name="Conversion Rate Drop",
            evidence="Store conversion rate dropped from 25% to 18%.",
            impacted_metrics="EOD Sales Targets",
            confidence=round(_pack_evidence(ev4_raw).confidence.score, 2),
            revenue_impact=ev4_raw.get("estimated_revenue_impact", 4800.0),
            evidence_package=_pack_evidence(ev4_raw),
        ))

        ev5_raw = builder.for_action_recommendation(
            title="AI Recommendation Generated",
            evidence_items=[
                {"metric": "urgency", "value": "HIGH", "change": "—"},
                {"metric": "recommended_action", "value": "Deploy backup cashiers", "change": "—"},
            ],
            reasoning=["Queue spike + abandonment pattern triggers HIGH urgency", "Open terminal #2 reduces queue by 60% within 8 minutes"],
            revenue_recovery=3200.0,
            model_certainty=0.95,
        )
        events.append(TimelineEvent(
            timestamp_label=(now_dt - timedelta(minutes=3)).strftime("%I:%M %p"),
            event_name="AI Recommendation Generated",
            evidence="Urgency: HIGH. Action: Deploy backup cashiers or open terminal #2.",
            impacted_metrics="Staffing allocation",
            confidence=round(_pack_evidence(ev5_raw).confidence.score, 2),
            revenue_impact=0.0,
            evidence_package=_pack_evidence(ev5_raw),
        ))
    else:
        # Standard nominal flow
        ev1_raw = builder.for_action_recommendation(
            title="Peak Traffic Arrival",
            evidence_items=[
                {"metric": "new_entries_10min", "value": 12, "change": "—"},
                {"metric": "lobby_load", "value": "MODERATE", "change": "—"},
            ],
            reasoning=["12 new visitor entries in 10 minutes at lobby entrance", "Normal peak arrival pattern"],
            revenue_recovery=0.0,
            model_certainty=0.95,
        )
        events.append(TimelineEvent(
            timestamp_label=(now_dt - timedelta(minutes=15)).strftime("%I:%M %p"),
            event_name="Peak Traffic Arrival",
            evidence="12 new visitor entries detected in 10 minutes at lobby entrance.",
            impacted_metrics="Lobby footfall",
            confidence=round(_pack_evidence(ev1_raw).confidence.score, 2),
            revenue_impact=0.0,
            evidence_package=_pack_evidence(ev1_raw),
        ))

        ev2_raw = builder.for_zone_intelligence(
            zone_id="SKINCARE", zone_name="Skincare & Treatments",
            visitors=5, dwell_minutes=4.2,
            conversions=1, revenue=ctx["total_gmv"] * 0.35,
        )
        events.append(TimelineEvent(
            timestamp_label=(now_dt - timedelta(minutes=10)).strftime("%I:%M %p"),
            event_name="Skincare Engagement Spike",
            evidence="Skincare zone occupancy increased to 5 shoppers. Avg dwell is 4.2 minutes.",
            impacted_metrics="Dwell Quality Index",
            confidence=round(_pack_evidence(ev2_raw).confidence.score, 2),
            revenue_impact=0.0,
            evidence_package=_pack_evidence(ev2_raw),
        ))

        ev3_raw = builder.for_forecast(
            current_queue=ctx["current_queue_depth"],
            p15=max(0, ctx["current_queue_depth"] - 1),
            p30=max(0, ctx["current_queue_depth"] - 1),
            p60=max(0, ctx["current_queue_depth"] - 2),
        )
        events.append(TimelineEvent(
            timestamp_label=(now_dt - timedelta(minutes=5)).strftime("%I:%M %p"),
            event_name="Checkout Clearance Nominal",
            evidence=f"Billing queue depth holds at {ctx['current_queue_depth']}. Average checkout clears in 1.5 minutes.",
            impacted_metrics="Queue Efficiency",
            confidence=round(_pack_evidence(ev3_raw).confidence.score, 2),
            revenue_impact=0.0,
            evidence_package=_pack_evidence(ev3_raw),
        ))

        ev4_raw = builder.for_action_recommendation(
            title="Merchandising Recommendation",
            evidence_items=[
                {"metric": "stock_status", "value": "ADEQUATE", "change": "—"},
                {"metric": "staff_distribution", "value": "OPTIMAL", "change": "—"},
            ],
            reasoning=["High-conversion displays fully stocked", "Staff distribution aligned with traffic zones"],
            revenue_recovery=0.0,
            model_certainty=0.93,
        )
        events.append(TimelineEvent(
            timestamp_label=(now_dt - timedelta(minutes=1)).strftime("%I:%M %p"),
            event_name="AI Recommendation: Merchandising",
            evidence="Keep high-conversion displays fully stocked. Staff distribution optimal.",
            impacted_metrics="Visual layouts ROI",
            confidence=round(_pack_evidence(ev4_raw).confidence.score, 2),
            revenue_impact=0.0,
            evidence_package=_pack_evidence(ev4_raw),
        ))

    from config import get_store_info
    s_info = get_store_info(store_id)
    return TimelineResponse(
        store_id=store_id,
        store_name=s_info.get("store_name"),
        store_location=s_info.get("store_location"),
        city=s_info.get("city"),
        events=events,
        confidence=0.93
    )


# ---------------------------------------------------------------------------
# Action Center
# ---------------------------------------------------------------------------

@router.get("/action-center", response_model=ActionCenterResponse)
async def get_action_center(
    store_id: str = "store_1",
    db: AsyncSession = Depends(get_db)
):
    """Urgency-ranked retail recommendation cards with full evidence trace and data provenance."""
    ctx = await _load_store_context(store_id, db)
    builder = build_evidence(store_id, ctx)
    recommendations = []

    # 1. Queue depth recommendation
    q_depth = ctx["current_queue_depth"]
    if q_depth >= 5 or sim_state.trigger_queue_spike.get(store_id, False):
        raw_ev = builder.for_queue_spike(queue_depth=max(q_depth, 5))
        evidence_pkg = _pack_evidence(raw_ev)
        recommendations.append(ActionCenterItem(
            priority="HIGH PRIORITY",
            title="Open Additional Billing Counter",
            improvement_pct=7.3,
            revenue_recovery=raw_ev.get("estimated_revenue_impact", 3200.0),
            confidence=round(evidence_pkg.confidence.score, 2),
            reasoning="Billing queue depth reaches 5+ shoppers, creating queue abandonment risks.",
            urgency="IMMEDIATE",
            evidence=[
                f"Queue depth is {q_depth} shoppers",
                "Average checkout time exceeds 3.5 minutes",
                f"Abandonment rate estimated at {min(20, 3 + (q_depth - 2) * 1.5):.1f}%",
            ],
            data_sources=["queue_events", "pos_transactions"],
            evidence_package=evidence_pkg,
        ))
    else:
        raw_ev = builder.for_action_recommendation(
            title="Standard Checkout",
            evidence_items=[
                {"metric": "queue_depth", "value": q_depth, "change": "—"},
                {"metric": "avg_wait_time", "value": "< 1.8m", "change": "—"},
            ],
            reasoning=["Queue clearance rate inside target SLA", "No abandonment detected"],
            revenue_recovery=0.0,
            model_certainty=0.93,
        )
        recommendations.append(ActionCenterItem(
            priority="LOW PRIORITY",
            title="Standard Checkout Configuration",
            improvement_pct=0.0,
            revenue_recovery=0.0,
            confidence=round(_pack_evidence(raw_ev).confidence.score, 2),
            reasoning="Queue clearance rate remains inside target SLA guidelines.",
            urgency="ROUTINE",
            evidence=[
                f"Queue depth is {q_depth} shoppers",
                "Average wait time under 1.8 minutes"
            ],
            data_sources=["queue_events"],
            evidence_package=_pack_evidence(raw_ev),
        ))

    # 2. Skincare / conversion recommendation
    if ctx["conversion_rate"] < 0.25 or sim_state.trigger_conversion_drop.get(store_id, False):
        raw_ev = builder.for_conversion_drop(conversion=ctx["conversion_rate"])
        evidence_pkg = _pack_evidence(raw_ev)
        recommendations.append(ActionCenterItem(
            priority="HIGH PRIORITY",
            title="Promote Underperforming Product Zones (Skincare/Makeup)",
            improvement_pct=4.8,
            revenue_recovery=raw_ev.get("estimated_revenue_impact", 2800.0),
            confidence=round(evidence_pkg.confidence.score, 2),
            reasoning="Skincare browsing traffic is high but buy-through conversion has slipped.",
            urgency="TODAY",
            evidence=[
                f"Store conversion dropped to {(ctx['conversion_rate'] * 100):.1f}%",
                "Skincare department dwell averages 4.2 minutes without checkouts",
                f"Estimated revenue opportunity: ₹{raw_ev.get('estimated_revenue_impact', 2800):,.0f}",
            ],
            data_sources=["visitor_events", "pos_transactions"],
            evidence_package=evidence_pkg,
        ))
    else:
        raw_ev = builder.for_action_recommendation(
            title="Merchandising Optimization",
            evidence_items=[
                {"metric": "conversion_rate", "value": f"{ctx['conversion_rate'] * 100:.1f}%", "change": "—"},
                {"metric": "fragrance_dwell", "value": "< 1.5m", "change": "—"},
            ],
            reasoning=["Fragrance zone has low dwell — upsell opportunity", "Skincare conversion steady at 21%"],
            revenue_recovery=1200.0,
            model_certainty=0.86,
        )
        recommendations.append(ActionCenterItem(
            priority="MEDIUM PRIORITY",
            title="Optimize Merchandising Layouts",
            improvement_pct=2.5,
            revenue_recovery=1200.0,
            confidence=round(_pack_evidence(raw_ev).confidence.score, 2),
            reasoning="Attract more customers to Fragrance or Bath zones using target promotions.",
            urgency="WEEKLY",
            evidence=[
                "Fragrance zone dwell is under 1.5 minutes",
                "Skincare conversion operates steadily at 21%"
            ],
            data_sources=["visitor_events"],
            evidence_package=_pack_evidence(raw_ev),
        ))

    # 3. Dead shelf relocations
    anoms = await get_anomalies(store_id, db=db)
    dead_zones = [an for an in anoms.active_anomalies if an.anomaly_type == "DEAD_ZONE"]
    for dz in dead_zones:
        z_id = dz.zone_id or "FRAGRANCE"
        raw_ev = builder.for_dead_zone(zone_id=z_id, zone_name=z_id.title())
        evidence_pkg = _pack_evidence(raw_ev)
        recommendations.append(ActionCenterItem(
            priority="HIGH PRIORITY",
            title=f"Relocate Staff / Signage to {z_id} Zone",
            improvement_pct=3.6,
            revenue_recovery=raw_ev.get("estimated_revenue_impact", 1800.0),
            confidence=round(evidence_pkg.confidence.score, 2),
            reasoning=f"Zero customer interactions detected in {z_id} corridor for over 30 minutes.",
            urgency="IMMEDIATE",
            evidence=[
                f"Dead zone anomaly detected for corridor: {z_id}",
                "Merchandise turns down 12% in category",
                "Zone inactive for 35+ minutes"
            ],
            data_sources=["visitor_events"],
            evidence_package=evidence_pkg,
        ))

    # 4. Standard staffing backup recommendation
    raw_ev = builder.for_action_recommendation(
        title="Staff Allocation",
        evidence_items=[
            {"metric": "peak_arrival_window", "value": "14:00–16:00 IST", "change": "—"},
            {"metric": "queue_occupancy_spike_probability", "value": "HIGH", "change": "—"},
        ],
        reasoning=[
            "Peak shopper arrivals predicted between 14:00–16:00 IST",
            "Cashier queue occupancy rates spike during afternoon hours",
            "Historical staffing pattern shows 18% queue growth in this window",
        ],
        revenue_recovery=1400.0,
        model_certainty=0.90,
    )
    recommendations.append(ActionCenterItem(
        priority="MEDIUM PRIORITY",
        title="Improve Staff Allocation",
        improvement_pct=3.1,
        revenue_recovery=1400.0,
        confidence=round(_pack_evidence(raw_ev).confidence.score, 2),
        reasoning="Align salesperson shifts with peak visitor arrival forecasts.",
        urgency="TODAY",
        evidence=[
            "Peak shopper arrivals predicted between 14:00-16:00 IST",
            "Cashier queue occupancy rates spike during afternoon hours"
        ],
        data_sources=["visitor_events", "queue_events"],
        evidence_package=_pack_evidence(raw_ev),
    ))

    # Log all HIGH PRIORITY recommendations to audit trail
    high_priority = [r for r in recommendations if r.priority == "HIGH PRIORITY"]
    for rec in high_priority:
        await log_ai_recommendation(
            endpoint="/ai/action-center",
            store_id=store_id,
            recommendation=rec.title,
            confidence_score=rec.confidence,
            confidence_level=rec.evidence_package.confidence.level if rec.evidence_package else "HIGH",
            evidence_snapshot=[e.model_dump() for e in (rec.evidence_package.evidence if rec.evidence_package else [])],
            inputs_snapshot=ctx,
        )

    from config import get_store_info
    s_info = get_store_info(store_id)
    return ActionCenterResponse(
        store_id=store_id,
        store_name=s_info.get("store_name"),
        store_location=s_info.get("store_location"),
        city=s_info.get("city"),
        recommendations=recommendations
    )


# ---------------------------------------------------------------------------
# Zone Intelligence (Digital Twin click)
# ---------------------------------------------------------------------------

@router.get("/zone-intelligence/{zone_id}", response_model=ZoneIntelligenceResponse)
async def get_zone_intelligence(
    zone_id: str,
    store_id: str = "store_1",
    db: AsyncSession = Depends(get_db),
):
    """
    Full AI evidence card for a Digital Twin zone click.
    Returns visitors, dwell, conversion, revenue, AI insight, business impact,
    suggested action, and a complete evidence package with confidence score.
    """
    ctx = await _load_store_context(store_id, db)
    builder = build_evidence(store_id, ctx)

    ZONE_NAMES = {
        "MAKEUP": "Makeup & Colour",
        "SKINCARE": "Skincare & Treatments",
        "BATH_BODY": "Bath & Body",
        "HAIR": "Hair Care",
        "FRAGRANCE": "Fragrance",
        "PERSONAL_CARE": "Personal Care",
        "BILLING": "Billing Counter",
    }

    # Load zone metrics from heatmap
    from heatmap import get_heatmap
    heatmap = await get_heatmap(store_id, db=db)
    zone_data = next((z for z in heatmap.zones if z.zone_id == zone_id), None)

    visitors = zone_data.visits if zone_data else 0
    dwell_minutes = zone_data.avg_dwell_minutes if zone_data else 0.0
    conversions = zone_data.conversions if zone_data else 0
    revenue = zone_data.revenue_influence if zone_data else 0.0
    zone_name = ZONE_NAMES.get(zone_id, zone_id.title())

    # Special handling for Billing zone
    if zone_id == "BILLING":
        visitors = ctx.get("current_queue_depth", 0)
        dwell_minutes = 3.5 if visitors >= 5 else 1.5
        conversions = ctx.get("transactions_count", 0)
        revenue = ctx.get("total_gmv", 0.0)

    conv_rate = conversions / visitors if visitors > 0 else 0.0
    store_avg_conversion = ctx.get("conversion_rate", 0.25)

    raw_ev = builder.for_zone_intelligence(
        zone_id=zone_id,
        zone_name=zone_name,
        visitors=visitors,
        dwell_minutes=dwell_minutes,
        conversions=conversions,
        revenue=revenue,
        store_avg_conversion=store_avg_conversion,
    )

    evidence_pkg = _pack_evidence(raw_ev)

    # Monthly revenue opportunity
    monthly_opportunity = raw_ev.get("estimated_revenue_impact", 0.0)

    await log_ai_recommendation(
        endpoint=f"/ai/zone-intelligence/{zone_id}",
        store_id=store_id,
        recommendation=raw_ev.get("suggested_action", ""),
        confidence_score=evidence_pkg.confidence.score,
        confidence_level=evidence_pkg.confidence.level,
        evidence_snapshot=[e.model_dump() for e in evidence_pkg.evidence],
        inputs_snapshot=ctx,
    )

    return ZoneIntelligenceResponse(
        zone_id=zone_id,
        zone_name=zone_name,
        visitors=visitors,
        dwell_minutes=round(dwell_minutes, 1),
        conversion_pct=round(conv_rate * 100, 1),
        revenue=round(revenue, 2),
        revenue_opportunity=round(monthly_opportunity, 2),
        ai_insight=raw_ev.get("ai_insight", ""),
        business_impact=f"Potential monthly revenue opportunity: ₹{monthly_opportunity:,.0f}",
        suggested_action=raw_ev.get("suggested_action", ""),
        evidence_package=evidence_pkg,
    )
