from pydantic import BaseModel, Field
from typing import List, Optional, Union, Dict, Any

class ChatRequest(BaseModel):
    message: str
    role: str = "Store Manager"

class QueryRequest(BaseModel):
    query: str
    role: str = "Store Manager"


# ---------------------------------------------------------------------------
# Evidence & Traceability Models
# ---------------------------------------------------------------------------

class EvidenceMetric(BaseModel):
    """A single supporting metric with its current value and delta change."""
    metric: str
    value: Union[float, int, str]
    change: Optional[str] = None  # e.g. "+300%", "-7.2%", "—"

class ConfidenceScore(BaseModel):
    """5-factor weighted confidence score for any AI output."""
    score: float                       # 0.0–1.0
    level: str                         # "HIGH" | "MEDIUM" | "LOW"
    data_volume: float = 0.0
    data_freshness: float = 0.0
    historical_consistency: float = 0.0
    model_certainty: float = 0.0
    signal_strength: float = 0.0

class DataProvenance(BaseModel):
    """Audit trail — exactly which data went into producing this output."""
    stores: List[str]
    events_analyzed: int
    sessions_analyzed: int
    transactions_analyzed: int = 0
    time_window: str = "Last 60 Minutes"

class EvidencePackage(BaseModel):
    """
    Complete evidence envelope attached to every AI output.
    Enables full traceability: conclusion → evidence → reasoning → source.
    """
    evidence: List[EvidenceMetric]
    reasoning_chain: List[str]
    source_data: DataProvenance
    confidence: ConfidenceScore
    estimated_revenue_impact: float = 0.0
    business_impact_severity: str = "MEDIUM"  # "HIGH" | "MEDIUM" | "LOW"


# ---------------------------------------------------------------------------
# AI Copilot
# ---------------------------------------------------------------------------

class AICopilotResponse(BaseModel):
    answer: str
    explanation: str
    supporting_evidence: List[str]
    confidence_score: float
    recommendation: str
    revenue_impact: float
    suggestions: List[str]
    data_sources: List[str]
    evidence_package: Optional[EvidencePackage] = None


# ---------------------------------------------------------------------------
# Revenue Opportunities
# ---------------------------------------------------------------------------

class RevenueOpportunity(BaseModel):
    factor: str
    estimated_leakage: float
    remedy: str
    confidence_score: float

class RevenueResponse(BaseModel):
    total_estimated_leakage: float
    opportunities: List[RevenueOpportunity]
    confidence_score: float


# ---------------------------------------------------------------------------
# Queue Forecast
# ---------------------------------------------------------------------------

class QueueForecastResponse(BaseModel):
    prediction_15m: int
    prediction_30m: int
    prediction_60m: int
    confidence_score: float
    forecast_drivers: List[str] = []
    evidence_package: Optional[EvidencePackage] = None


# ---------------------------------------------------------------------------
# Root Cause Analysis
# ---------------------------------------------------------------------------

class RCADiagnosis(BaseModel):
    anomaly_id: str
    anomaly_type: str
    root_cause: str
    confidence_score: float
    remedy_impact: str

class RCAResponse(BaseModel):
    diagnoses: List[RCADiagnosis]
    aggregate_confidence: float


# ---------------------------------------------------------------------------
# Store Health
# ---------------------------------------------------------------------------

class StoreHealthResponse(BaseModel):
    score: int
    classification: str  # "EXCELLENT" | "GOOD" | "ATTENTION" | "CRITICAL"
    conversion_performance: int
    revenue_performance: int
    queue_efficiency: int
    customer_experience: int
    operational_stability: int
    camera_health: int
    trend: str
    confidence_score: float
    revenue_opportunity: float
    ai_insight: str
    ai_explanation: str
    factors: List[str]
    evidence_package: Optional[EvidencePackage] = None


# ---------------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------------

class BenchmarkMetric(BaseModel):
    store_id: str
    store_name: str
    total_gmv: float
    unique_visitors: int
    conversion_rate: float
    current_queue_depth: int

class BenchmarkResponse(BaseModel):
    date: str
    benchmarks: List[BenchmarkMetric]
    ai_explanation: str = ""


# ---------------------------------------------------------------------------
# Conversion Predictions
# ---------------------------------------------------------------------------

class PredictionMetric(BaseModel):
    metric_name: str
    current_value: float
    predicted_eod_value: float
    confidence_score: float
    expected_operational_impact: str = "No immediate operational risk"
    timeframe: str = "EOD"

class PredictionsResponse(BaseModel):
    date: str
    predictions: List[PredictionMetric]


# ---------------------------------------------------------------------------
# Audit Logs
# ---------------------------------------------------------------------------

class AuditLogItem(BaseModel):
    id: int
    timestamp: str
    user_role: str
    action: str
    resource: str
    metadata: str

class AuditLogsResponse(BaseModel):
    logs: List[AuditLogItem]


# ---------------------------------------------------------------------------
# Re-ID Diagnostics
# ---------------------------------------------------------------------------

class ReidDiagnosticsResponse(BaseModel):
    total_ingested: int
    duplicates_suppressed: int
    reentries_converted: int
    cross_camera_matches: int
    average_confidence: float


# ---------------------------------------------------------------------------
# Daily Summary
# ---------------------------------------------------------------------------

class DailySummaryResponse(BaseModel):
    store_id: Optional[str] = None
    store_name: Optional[str] = None
    store_location: Optional[str] = None
    city: Optional[str] = None
    summary: str
    visitors: int
    revenue: float
    conversion: float
    top_opportunity: str
    top_risk: str
    estimated_revenue_recovery: float
    recommended_action: str
    confidence_score: float
    data_sources: List[str]
    evidence_package: Optional[EvidencePackage] = None


# ---------------------------------------------------------------------------
# Journey Replay
# ---------------------------------------------------------------------------

class JourneyStep(BaseModel):
    timestamp: str
    zone_id: Optional[str] = None
    zone_name: str
    event_type: str
    description: str
    x: float
    y: float

class JourneyReplayResponse(BaseModel):
    visitor_id: str
    steps: List[JourneyStep]
    duration_seconds: int
    ai_character_profile: str


# ---------------------------------------------------------------------------
# Store Rankings
# ---------------------------------------------------------------------------

class StoreRankItem(BaseModel):
    store_id: str
    store_name: str
    health_score: int
    rank: int
    status: str

class StoreRankingsResponse(BaseModel):
    rankings: List[StoreRankItem]


# ---------------------------------------------------------------------------
# Command Center
# ---------------------------------------------------------------------------

class CommandCenterInsight(BaseModel):
    insight_type: str  # "REVENUE" | "CONVERSION" | "QUEUE" | "RISK"
    title: str
    summary: str
    confidence_score: float
    business_impact: str
    recommended_action: str
    evidence_package: Optional[EvidencePackage] = None

class CommandCenterResponse(BaseModel):
    store_id: str
    store_name: Optional[str] = None
    store_location: Optional[str] = None
    city: Optional[str] = None
    daily_summary: str
    revenue_summary: str
    conversion_summary: str
    queue_summary: str
    insights: List[CommandCenterInsight]


# ---------------------------------------------------------------------------
# Revenue Leakage
# ---------------------------------------------------------------------------

class LeakageItem(BaseModel):
    issue_name: str
    category: str  # "HIGH_DWELL_LOW_CONV" | "QUEUE_ABANDONMENT" | "DEAD_SHELF" | "NO_PURCHASE_TRAFFIC" | "BILLING_DELAYS" | "UNDERPERFORMING_ZONES"
    description: str
    estimated_loss_daily: float
    missed_conversions: int
    monthly_impact: float
    business_impact_rank: int
    root_cause: str
    revenue_impact: float
    business_impact: str
    recommended_action: str
    confidence_score: float
    evidence_package: Optional[EvidencePackage] = None

class RevenueLeakageResponse(BaseModel):
    store_id: str
    store_name: Optional[str] = None
    store_location: Optional[str] = None
    city: Optional[str] = None
    total_leakage_daily: float
    total_leakage_weekly: float
    total_leakage_monthly: float
    recoverable_revenue: float
    leakages: List[LeakageItem]
    confidence: float
    data_sources: List[str]


# ---------------------------------------------------------------------------
# Explain This
# ---------------------------------------------------------------------------

class ExplainResponse(BaseModel):
    metric_name: str
    answer: str
    evidence: List[str]
    revenue_impact: float
    confidence_score: float
    data_sources: List[str]
    evidence_package: Optional[EvidencePackage] = None
    reasoning_chain: List[str] = []


# ---------------------------------------------------------------------------
# Decision Timeline
# ---------------------------------------------------------------------------

class TimelineEvent(BaseModel):
    timestamp_label: str
    event_name: str
    evidence: str
    impacted_metrics: str
    confidence: float
    revenue_impact: float
    evidence_package: Optional[EvidencePackage] = None

class TimelineResponse(BaseModel):
    store_id: str
    store_name: Optional[str] = None
    store_location: Optional[str] = None
    city: Optional[str] = None
    events: List[TimelineEvent]
    confidence: float


# ---------------------------------------------------------------------------
# Action Center
# ---------------------------------------------------------------------------

class ActionCenterItem(BaseModel):
    priority: str
    title: str
    improvement_pct: float
    revenue_recovery: float
    confidence: float
    reasoning: str
    urgency: str
    evidence: List[str]
    data_sources: List[str]
    evidence_package: Optional[EvidencePackage] = None

class ActionCenterResponse(BaseModel):
    store_id: str
    store_name: Optional[str] = None
    store_location: Optional[str] = None
    city: Optional[str] = None
    recommendations: List[ActionCenterItem]


# ---------------------------------------------------------------------------
# Zone Intelligence (Digital Twin click)
# ---------------------------------------------------------------------------

class ZoneIntelligenceResponse(BaseModel):
    """Full AI evidence card returned when clicking a zone in the Digital Twin."""
    zone_id: str
    zone_name: str
    visitors: int
    dwell_minutes: float
    conversion_pct: float
    revenue: float
    revenue_opportunity: float
    ai_insight: str
    business_impact: str
    suggested_action: str
    evidence_package: EvidencePackage
