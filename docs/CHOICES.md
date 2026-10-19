# VisionRetail IQ — Architectural Trade-offs & Engineering Choices

This document logs the critical architectural decisions, engineering trade-offs, and design selections made during the implementation of VisionRetail IQ. Each log details the alternatives considered, the choices made, the underlying technical rationale, and the Claude AI suggestions where we aligned or explicitly disagreed.

## Executive Summary: Engineering & AI Decisions

| Category | AI Suggestion Considered | Final Engineering Decision | Core Rationale / Trade-off Accepted |
| :--- | :--- | :--- | :--- |
| **Detection Model** | Use heavy models (YOLOv8m/ViT) for max precision. | **YOLOv8n (Nano)** | Guarantees high-FPS real-time processing on edge CPU hardware; prioritizes recall over max precision. |
| **Tracking Model** | DeepSORT for appearance-based tracking. | **ByteTrack** | Extremely fast spatial heuristic tracking; maintains IDs during partial occlusions without GPU cost. |
| **Event Schema** | Post-hoc batching of `ZONE` events upon exit. | **Real-Time Streaming (30s intervals)** | Enables live Digital Twin updates; prevents data loss on stream truncation. |
| **API Architecture** | Monolithic REST polling architecture. | **Event-Driven Asynchronous Pipeline** | Decouples CV processing from business logic; uses JSONL event streams to prevent frame dropping. |
| **Database** | PostgreSQL for enterprise scalability. | **SQLite + Async SQLAlchemy** | Zero-configuration file-based persistence perfectly suited for single-store edge deployments. |
| **Caching** | Redis for all state, including queue depth. | **Redis (Live Occupancy Only)** | Sub-millisecond latency for live dashboards; queue state is strictly written to SQLite for auditability. |
| **AI Overrides** | Use Facial Recognition for customer Re-ID. | **Bounding-Box Height Signature** | Strict privacy compliance (no biometric data collected); effective for short-window (30m) re-entry matching. |


## Decision 1: Object Detection & Tracking Pipeline

### Options Evaluated
* **YOLOv8n**: 8.0ms/frame on standard CPU, 640px input size, strong bounding-box regression, optimized for edge devices.
* **YOLOv8m**: 22.0ms/frame on CPU, higher mean Average Precision (mAP), but requires GPU acceleration to run in real-time.
* **RT-DETR (Real-Time DEtection Transformer)**: 18.0ms/frame on CPU, excellent global attention, but heavy memory overhead.
* **MediaPipe Pose**: 5.0ms/frame, but poor occlusion recovery in dense crowds.

### AI Suggestion (Claude)
Claude recommended utilizing **YOLOv8m** to achieve maximum precision under occlusions, especially near the billing queues and busy storefront corridors on high-traffic days.

### Choice Made
**YOLOv8n with a tuned confidence threshold of 0.35 + ByteTrack.**

### Engineering Rationale
* **Hardware & Resource Limits**: The target environment is a containerized edge system (Docker Compose). Since GPU acceleration is not guaranteed, YOLOv8n was chosen to ensure the pipeline runs comfortably in real-time (at 15fps or 30fps) on a standard CPU.
* **Recall Priority in Retail**: In retail analytics, recall is valued over precision. Missing a customer completely (false negative) causes a systematic undercount in conversion funnels. False positives can be filtered downstream. We set the detection threshold to `0.35` (higher than the default `0.25`) to minimize false classifications caused by reflections on shiny product displays, while maintaining high recall.
* **ByteTrack Association**: Unlike basic tracker models like SORT, ByteTrack keeps tracks alive even when confidence dips below detection thresholds during partial occlusions. If a customer stands behind a promotional display shelf and reappears, ByteTrack retains their unique `visitor_id`, preventing session duplication.

## Decision 2: Zone Dwell Event Emission Strategy

### Options Evaluated
* **Post-hoc Batching**: Emit a single `ZONE_DWELL` event after a visitor crosses a `ZONE_EXIT` line, recording the total accumulated time.
* **Real-Time Streaming**: Emit a `ZONE_DWELL` event periodically (every 30 seconds of continuous dwell time) while the visitor remains in the zone.

### AI Suggestion (Claude)
Claude suggested using the **Post-hoc Batching** approach. It argued that batching minimizes the total database write load and keeps the SQL schema simple, since each zone visit generates exactly one row.

### Choice Made
**Real-Time Streaming at 30-Second Intervals.**

### Engineering Rationale
* **Interactive Live Monitoring**: Post-hoc batching creates a massive telemetry delay. If a customer dwells in the Skincare zone for 15 minutes, the dashboard would show zero activity for that customer until they exit. Real-time streaming allows the Digital Twin heatmap and occupancy counts to update instantly while the customer is still browsing.
* **Truncation Resilience**: If the CCTV video stream cuts off unexpectedly, a post-hoc batch system loses all dwell evidence for active shoppers. The 30-second streaming approach ensures that at least partial dwell events are flushed and preserved.
* **Scalable Aggregations**: The SQL aggregation layer is indexed on `dwell_ms`, allowing it to quickly sum raw metrics:
  $$\text{Dwell Time} = \sum (\text{dwell\_ms})$$
  This gives a highly accurate representation of store engagement with minimal compute overhead.


## Decision 3: Storage Engine & In-Memory State

### Options Evaluated
* **PostgreSQL**: Production-grade concurrency, robust JSON indexing, but requires a separate running container and migrations setup.
* **DuckDB**: Columnar storage engine optimized for complex analytics queries, but writes are non-concurrent.
* **SQLite + Async SQLAlchemy**: Zero configuration, file-based, async, fast read throughput.
* **Redis**: In-memory speed, but lacks robust relational querying.

### AI Suggestion (Claude)
Claude recommended utilizing **PostgreSQL** to demonstrate enterprise-grade engineering, combined with **Redis** to track live queue depths and active store occupancy.

### Choice Made
**SQLite + Async SQLAlchemy + Redis for active occupancy caching.**

### Engineering Rationale
* **Scale Suitability**: A typical retail store processes approximately 100–300 visits and 1,000–5,000 CCTV events daily. The database file size after a year of operations remains under 150MB. SQLite handles this volume easily.
* **Deployment Simplicity**: A SQLite database runs locally in the API workspace directory with zero-config database provisioning, satisfying the hackathon gate requirements.
* **SQL Abstraction Layer**: By using SQLAlchemy as an ORM layer, migrating to PostgreSQL in a multi-store production deployment requires only changing the `DATABASE_URL` environment variable.
* **We Disagreed with Claude on Queue Tracking**: Claude suggested using Redis for queue depth state tracking. We rejected this because queue events must be historically auditable. Instead, we write `BILLING_QUEUE_JOIN` and `BILLING_QUEUE_LEAVE` events directly to SQLite. Redis is used solely to store the transient real-time customer layout coordinates for the Digital Twin map view.


## Decision 4: Cross-Camera Deduplication

### Options Evaluated
* **Homographic Coordinate Mapping (IoU-based)**: Project bounding boxes onto a shared 2D ground plan. Requires complex camera calibration and lens distortion correction.
* **Deep Re-ID Embeddings**: Run lightweight CNN inference (e.g. MobileNet) on crop embeddings to match identities across overlapping feeds. Extremely slow (~45ms per check) and resource-heavy.
* **Spatial-Temporal Thresholding**: Suppress detections on secondary cameras if a matching track ID appeared on the primary entry camera within a tight 3-second window.

### AI Suggestion (Claude)
Claude proposed running **Appearance Re-ID** on every frame transition to match crops across feeds using a lightweight MobileNet.

### Choice Made
**Temporal Track-ID Suppression (Entry camera authoritative).**

### Engineering Rationale
* **Zero Inference Latency**: Temporal track suppression runs in $0.001\text{ms}$ vs $45\text{ms}$ for appearance matching. This allows the system to scale without needing expensive GPU servers at the edge.
* **Shared Detection Context**: When cameras share overlapping fields of view (e.g., store entrance), the tracker engine tracks the same object across the overlap. An event emitted by `CAM_FLOOR_01` is ignored if `CAM_ENTRY_01` registered a crossing for that ID within 3 seconds, eliminating double-counting at the conversion funnel entry gate.


## Decision 5: Bounding Box Height Re-ID (Claude Disagreement)

### Key Disagreement
Claude suggested using **Face Recognition** as a fallback Re-ID model to identify returning shoppers.

### My Choice
**Bounding-Box Height Signature within a 30-Minute Temporal Window.**

### Engineering Rationale
* **Privacy Compliance**: CCTV deployments in India must comply with strict privacy regulations. Using facial recognition on shoppers without explicit consent is a significant legal risk. Facial blurring is often applied directly at the camera level, rendering facial recognition models useless.
* **Efficiency and Robustness**: Bounding-box height signatures are calculated using simple coordinates, requiring zero additional GPU compute. Comparing these signatures within a 30-minute window (typical return time for a customer) provides a reliable Re-ID alternative that is fully privacy-compliant.


## Decision 6: Temporal Sync Logic

### Options Evaluated
* **Server-Time Tagging**: Use `datetime.utcnow()` at the moment of ingestion.
* **Frame-Offset Calculation**: Anchored UTC timestamp using the formula:
  $$\text{Timestamp} = \text{Clip Start Time} + \frac{\text{Frame Offset}}{\text{FPS}}$$

### AI Suggestion (Claude)
Claude initially suggested using **Server-Time Tagging** because it is simple to implement in code and does not rely on video metadata.

### Choice Made
**Frame-Offset Calculation.**

### Engineering Rationale
* **Offline Ingestion Support**: Server-time tagging creates issues when video clips are processed offline or replayed. If a clip from 12:00 PM is processed at 4:00 PM, server-time tagging will tag the events with 4:00 PM. Frame-offset calculations ensure that events are always accurately anchored to the actual operating time of the footage.
* **Replay Consistency**: This approach guarantees that replaying a clip will generate identical timestamps every time, maintaining database integrity and event consistency.
