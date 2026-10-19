# Pipeline Understanding — VisionRetail IQ

This document details the inner workings of VisionRetail IQ's computer vision and behavioral analysis pipeline (`pipeline/`).


## Pipeline Overview

The pipeline extracts retail insights from CCTV clips by combining object detection, multi-object tracking, custom heuristics, and spatial analysis:

```
[Raw Frame]
    |
    v
[YOLOv8n Person Detect] --> (Class 0 Bounding Boxes)
    |
    v
[ByteTrack Multi-Object Tracker] --> (Stable Track IDs)
    |
    +--> [Staff Classifier] --> HSV torso crop analysis -> determines is_staff
    |
    +--> [Direction Detector] --> Tripwire Y=0.5 crossing -> ENTRY/EXIT events
    |
    +--> [Deduplicator] --> Suppresses overlaps across cameras
    |
    v
[Visitor Tracker] --> BB Height Re-ID & 30-min window -> Assigns visitor_id
    |
    v
[Event Emitter] --> Validates structure & flushes JSONL events
```


## 1. File & Camera Fallback Resolver

In physical deployments, video clip file names or directory hierarchies often differ from layout schemas. The pipeline implements a dynamic resolver:
* If `store_id == "STORE_MUM_001"`, it automatically searches the `resources/clips/Store 2` folder.
* If a camera filename (e.g., `CAM 1_MUM.mp4` for `CAM_ENTRY_02`) does not exist, the script lists all MP4 files and selects fallbacks based on camera type:
  * **`entry_exit`**: Searches for files containing `"entry 1"` or `"entry"`.
  * **`floor`**: Searches for files containing `"zone"` or `"floor"`.
  * **`billing`**: Searches for files containing `"billing"` or `"cash"`.


## 2. Detection & Multi-Object Tracking

* **Model**: YOLOv8n (`yolov8n.pt`).
* **Detection Parameters**:
  * `classes=[0]` (Detects only class `0` - person).
  * `conf=0.35` (Higher confidence cutoff to prevent false positives on reflections or mannequin displays).
  * `iou=0.4` (Intersection-over-Union threshold for NMS).
  * `tracker="bytetrack.yaml"` (ByteTrack tracker configuration).
* **Frame Skipping**: The pipeline processes every **3rd frame** (`PROCESS_EVERY_N = 3`) to double inference speed while maintaining tracking stability.


## 3. Staff Classifier Engine

Identifies active sales associates instantly using an HSV color-matching approach on the person's torso region:
* **Torso Extraction**: Extracts the vertical crop from 25% to 65% of the overall bounding box height (avoids head hair and shoes).
* **HSV Uniform Matching**: Converts BGR to HSV and tests coverage against calibrated uniforms:
  * Dark navy/blue uniform
  * White shirts / lab coats
  * Purplle brand violet/magenta
* **Threshold**: If over **35%** of the torso pixels fall into any of these uniform ranges, the person is classified as `is_staff = True`.
* **Caching**: Caches classification results by `track_id` for 30 frames to avoid redundant processing on every frame.


## 4. Direction & Tripwire Math

Detects physical entrants and exits using a virtual tripwire on the entry camera:
* **Tripwire Location**: Defined at a normalized vertical coordinate `tripwire_y = 0.5` (center of the video screen).
* **Crossing Logic**: Triggers when the bounding box center (`cy`) crosses the tripwire line:
  * Crosses downward (`prev_cy < 0.5 <= cy`): **ENTRY**
  * Crosses upward (`prev_cy > 0.5 >= cy`): **EXIT**
* **Debouncing**: Requires a minimum of 5 consistent frames (`min_frames = 5`) of directional movement to suppress event noise.


## 5. Visitor Re-Identification (Re-ID)

Maintains visitor identity across camera handoffs and returning store visits without violating privacy (does not use facial recognition):
* **Handoff Tracking**: Tracks heights (`bbox_norm[3] - bbox_norm[1]`) and timestamps.
* **Secondary Handoff**: If a visitor appears on a secondary camera within 5 minutes, their bounding-box height is checked against active sessions. A matched height within `0.10` tolerance assigns the existing `visitor_id`.
* **Re-entry Window**: If a visitor enters the entry camera within 30 minutes of a registered exit, the pipeline tests height similarity (`0.05` tolerance). If matched, it restores their previous `visitor_id` and fires a **`REENTRY`** event.
* **Timezone Math**: Time subtraction matches timezone offsets (`now.tzinfo`) dynamically to prevent offset-naive/aware exceptions.
