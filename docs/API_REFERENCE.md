# API Reference — VisionRetail IQ

This document describes all API endpoints exposed by the FastAPI application server, detailing requests, responses, schemas, and payload examples.


## 1. Ingestion Endpoint

### Ingest CV Events
* **Endpoint**: `POST /events/ingest`
* **Content-Type**: `application/json`
* **Description**: Receives batch lists of computer vision events. This endpoint is idempotent (skips duplicate `event_id`s) and supports partial successes.

#### Request Payload Example
```json
{
  "events": [
    {
      "event_id": "c71bf9e8-4628-4efc-80a1-9a7ab96dc5b1",
      "store_id": "STORE_MUM_001",
      "camera_id": "CAM_ENTRY_02",
      "visitor_id": "VIS_bca382",
      "event_type": "ENTRY",
      "timestamp": "2026-04-10T12:35:10Z",
      "zone_id": null,
      "dwell_ms": 0,
      "is_staff": false,
      "confidence": 0.941,
      "metadata": {
        "session_seq": 1,
        "sku_zone": null
      }
    }
  ]
}
```

#### Response Example (200 OK)
```json
{
  "status": "success",
  "processed": 1,
  "accepted": 1,
  "rejected": 0,
  "duplicate": 0
}
```


## 2. Store Analytics Endpoints

### Get Store Metrics
* **Endpoint**: `GET /stores/{store_id}/metrics`
* **Query Parameters**:
  * `date` (string, optional, ISO-8601 format: `YYYY-MM-DD`, defaults to today)
* **Description**: Returns key performance indicators for the specified store.

#### Response Example (200 OK)
```json
{
  "store_id": "STORE_MUM_001",
  "date": "2026-04-10",
  "unique_visitors": 142,
  "conversion_rate": 0.183,
  "avg_dwell_minutes": 8.4,
  "current_queue_depth": 1,
  "queue_abandonment_rate": 0.052,
  "total_gmv": 78717.5
}
```


### Get Funnel Analytics
* **Endpoint**: `GET /stores/{store_id}/funnel`
* **Description**: Computes conversion stages for unique visitors. Does not double-count re-entries.

#### Response Example (200 OK)
```json
{
  "store_id": "STORE_MUM_001",
  "total_entered": 142,
  "browsed_floor": 138,
  "reached_checkout": 32,
  "purchased": 26,
  "conversion_percentage": 18.31
}
```


### Get Zone Heatmap
* **Endpoint**: `GET /stores/{store_id}/heatmap`
* **Description**: Renders visitor occupancies and analytics indicators for all zones.

#### Response Example (200 OK)
```json
{
  "store_id": "STORE_MUM_001",
  "zones": [
    {
      "zone_id": "MAKEUP",
      "visits": 112,
      "conversions": 14,
      "abandonment": 2,
      "revenue_influence": 24800.0,
      "normalized_intensity": 100
    },
    {
      "zone_id": "SKINCARE",
      "visits": 94,
      "conversions": 19,
      "abandonment": 1,
      "revenue_influence": 31450.0,
      "normalized_intensity": 83
    }
  ]
}
```


## 3. Operational Observability Endpoints

### Get Store Anomalies
* **Endpoint**: `GET /stores/{store_id}/anomalies`
* **Description**: Returns live operational alert diagnostics alongside recommended actions.

#### Response Example (200 OK)
```json
{
  "store_id": "STORE_MUM_001",
  "anomalies": [
    {
      "type": "QUEUE_SPIKE",
      "severity": "CRITICAL",
      "message": "Billing queue depth reached 5 visitors. High risk of queue abandonment.",
      "suggested_action": "Deploy backup biller Kabir Sen to counter 2 immediately.",
      "timestamp": "2026-04-10T14:22:10Z"
    }
  ]
}
```


### System Health
* **Endpoint**: `GET /health`
* **Description**: Provides status checks for database connectivity and camera hardware telemetry.

#### Response Example (200 OK)
```json
{
  "status": "healthy",
  "database": "connected",
  "cameras": {
    "CAM_ENTRY_02": {
      "status": "active",
      "fps": 24.8,
      "event_rate_epm": 12.4,
      "stale_feed_alert": false
    },
    "CAM_FLOOR_02": {
      "status": "active",
      "fps": 24.9,
      "event_rate_epm": 31.8,
      "stale_feed_alert": false
    }
  }
}
```


## 4. AI & Executive Decision Endpoints

### Executive Command Center
* **Endpoint**: `GET /ai/command-center`
* **Query Parameters**:
  * `store_id` (string, required)
* **Description**: Returns AI-generated operational and business summaries.

#### Response Example (200 OK)
```json
{
  "executive_summary": "Overall performance is strong with GMV up 15% due to high skincare conversions. Active checkout queues are stable, but makeup engagement remains lower than historical averages.",
  "performance_insights": [
    {
      "insight_id": "ins_01",
      "metric": "Footfall & Conversion",
      "observation": "Footfall peaked at 14:00 with conversion rate holding steady at 18%.",
      "recommendation": "Maintain standard staff distribution.",
      "impact": "Stable sales velocity",
      "confidence": 0.95
    }
  ]
}
```


### Revenue Leakage Detector
* **Endpoint**: `GET /ai/revenue-leakage`
* **Query Parameters**:
  * `store_id` (string, required)
* **Description**: Ranks and logs operational losses due to queue dropouts or layout cold spots.

#### Response Example (200 OK)
```json
{
  "total_estimated_leakage_inr": 8500.0,
  "leaks": [
    {
      "leak_id": "leak_queue_abandon",
      "title": "Checkout Queue Abandonment",
      "metric": "Dwell without Purchase",
      "estimated_loss_inr": 4800.0,
      "recommendation": "Deploy backup salespeople during queue spikes (>3 persons).",
      "severity": "HIGH"
    }
  ]
}
```


### AI Copilot Chat
* **Endpoint**: `POST /ai/copilot/chat`
* **Description**: Processes user natural language questions about store metrics and returns AI responses.

#### Request Example
```json
{
  "message": "What was the conversion rate for Store 2 today?",
  "store_id": "STORE_MUM_001"
}
```

#### Response Example
```json
{
  "reply": "The conversion rate for Store 2 (Colaba Mumbai) today is 18.3%, based on 142 unique visitors and 26 purchasing checkout events."
}
```


## 5. Simulation Control Endpoint

### Post Simulator Command
* **Endpoint**: `POST /simulator/control`
* **Description**: Sets presenter flags to trigger real-time demonstration overrides.

#### Request Example
```json
{
  "store_id": "STORE_MUM_001",
  "action": "trigger_queue_spike",
  "value": true
}
```

#### Response Example
```json
{
  "status": "success",
  "message": "Action trigger_queue_spike set to True for STORE_MUM_001"
}
```
