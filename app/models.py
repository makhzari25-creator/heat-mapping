from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from enum import Enum
import uuid


class EventType(str, Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    ZONE_ENTER = "ZONE_ENTER"
    ZONE_EXIT = "ZONE_EXIT"
    ZONE_DWELL = "ZONE_DWELL"
    BILLING_QUEUE_JOIN = "BILLING_QUEUE_JOIN"
    BILLING_QUEUE_ABANDON = "BILLING_QUEUE_ABANDON"
    REENTRY = "REENTRY"


class EventMetadata(BaseModel):
    queue_depth: Optional[int] = None
    sku_zone: Optional[str] = None
    session_seq: int = 1
    amount: Optional[float] = None
    salesperson_id: Optional[int] = None
    floor: Optional[str] = None      # which floor the camera sits on
    plan_x: Optional[float] = None   # foot position on the floor plan (0-1)
    plan_y: Optional[float] = None


class StoreEvent(BaseModel):
    event_id: str
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: EventType
    timestamp: str
    zone_id: Optional[str] = None
    dwell_ms: int = 0
    is_staff: bool
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: EventMetadata

    @field_validator("event_id")
    @classmethod
    def validate_uuid(cls, v):
        try:
            uuid.UUID(str(v))
        except ValueError:
            raise ValueError("event_id must be valid UUID v4")
        return v

    @field_validator("visitor_id")
    @classmethod
    def validate_visitor_id(cls, v):
        if not v.startswith("VIS_"):
            raise ValueError("visitor_id must start with VIS_")
        return v


class IngestRequest(BaseModel):
    events: List[StoreEvent] = Field(max_length=500)


class IngestResponse(BaseModel):
    accepted: int
    rejected: int
    duplicate: int
    errors: List[dict] = []


class ZoneDwell(BaseModel):
    zone_id: str
    zone_name: str
    avg_dwell_minutes: float
    visitor_count: int


class DataConfidence(BaseModel):
    confidence_score: float
    confidence_level: str  # "HIGH" | "MEDIUM" | "LOW"
    supporting_sample_size: int


class StoreMetrics(BaseModel):
    store_id: str
    store_name: Optional[str] = None
    store_location: Optional[str] = None
    city: Optional[str] = None
    date: str
    unique_visitors: int
    conversion_rate: float
    avg_dwell_per_zone: List[ZoneDwell]
    current_queue_depth: int
    abandonment_rate: float
    as_of: str
    total_gmv: float = 0.0
    total_orders: int = 0
    avg_basket_value: float = 0.0
    data_confidence: Optional[DataConfidence] = None


class FunnelStage(BaseModel):
    stage: str
    count: int
    drop_off_pct: float


class FunnelResponse(BaseModel):
    store_id: str
    store_name: Optional[str] = None
    store_location: Optional[str] = None
    city: Optional[str] = None
    date: str
    stages: List[FunnelStage]
    total_sessions: int
    data_confidence: Optional[DataConfidence] = None


class ZoneHeatmapItem(BaseModel):
    zone_id: str
    zone_name: str
    visit_frequency: float
    avg_dwell_minutes: float
    data_confidence: str  # "high" | "low"
    visits: int = 0
    conversions: int = 0
    abandonment: int = 0
    revenue_influence: float = 0.0
    floor: Optional[str] = None


class HeatmapResponse(BaseModel):
    store_id: str
    store_name: Optional[str] = None
    store_location: Optional[str] = None
    city: Optional[str] = None
    zones: List[ZoneHeatmapItem]
    data_confidence: Optional[DataConfidence] = None
    floors: List[str] = []
    floor: Optional[str] = None



class AnomalySeverity(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


class AnomalyItem(BaseModel):
    anomaly_id: str
    anomaly_type: str
    severity: AnomalySeverity
    description: str
    suggested_action: str
    detected_at: str
    zone_id: Optional[str] = None
    metric_value: Optional[float] = None
    threshold_value: Optional[float] = None


class AnomaliesResponse(BaseModel):
    store_id: str
    store_name: Optional[str] = None
    store_location: Optional[str] = None
    city: Optional[str] = None
    active_anomalies: List[AnomalyItem]
    as_of: str


class StoreHealthItem(BaseModel):
    store_id: str
    store_name: Optional[str] = None
    store_location: Optional[str] = None
    city: Optional[str] = None
    status: str
    last_event_timestamp: Optional[str] = None
    lag_minutes: Optional[float] = None
    warning: Optional[str] = None


class CameraHealthItem(BaseModel):
    camera_id: str
    status: str
    last_event_timestamp: Optional[str] = None
    lag_minutes: Optional[float] = None
    fps: float = 0.0
    event_rate_epm: float = 0.0
    stale_feed_alert: bool = False


class HealthResponse(BaseModel):
    service: str
    status: str
    uptime_seconds: float
    stores: List[StoreHealthItem]
    db_ok: bool
    redis_ok: bool = False
    redis_latency_ms: Optional[float] = None
    environment: str = "production"
    version: str
    processing_latency_ms: Optional[float] = None
    event_ingestion_rate_epm: Optional[float] = None
    cameras: Optional[List[CameraHealthItem]] = None

