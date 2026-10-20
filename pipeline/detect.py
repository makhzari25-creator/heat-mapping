"""
Main detection + tracking pipeline.
Processes CCTV clips from resources/clips/ and emits JSONL events
conforming to the required schema.

Real store context:
  - Store: Brigade Bangalore (ST1008) -> event schema uses store_1
  - Staff: 5 identified salespersons (kasthuri v, Zufishan Khazra, Shashikala,
           Naziya Begum, Priya v) — all wear store uniform
  - Operating hours: 12:00-22:00 IST (06:30-16:30 UTC) on 10-04-2026
  - Zones: MAKEUP, SKINCARE, BATH_BODY, HAIR, FRAGRANCE, PERSONAL_CARE
"""
import cv2
import json
import uuid
import argparse
import numpy as np
from pathlib import Path
from datetime import datetime, timezone, timedelta

from tracker import VisitorTracker
from staff_classifier import StaffClassifier
from direction_detector import DirectionDetector
from pos_correlator import POSCorrelator
from emit import EventEmitter
from dedup import CrossCameraDeduplicator


def load_store_layout(layout_path: str) -> dict:
    with open(layout_path) as f:
        return json.load(f)


def load_pos_data(csv_path: str) -> list:
    import pandas as pd
    df = pd.read_csv(csv_path)
    records = []
    for _, row in df.iterrows():
        if 'order_time' in df.columns:
            # Real Brigade Bangalore format
            try:
                ts_str = f"{row['order_date']} {row['order_time']}"
                ts = datetime.strptime(ts_str, "%d-%m-%Y %H:%M:%S")
                ts = ts.replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))  # IST
                records.append({
                    "store_id": "store_1",  # Map ST1008 -> store_1
                    "transaction_id": str(row.get('invoice_number', row.get('order_id', ''))),
                    "timestamp": ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "basket_value_inr": float(row.get('total_amount', 0) or 0),
                })
            except Exception as e:
                print(f"[WARN] Could not parse POS row: {e}")
        else:
            # Challenge format: store_id, transaction_id, timestamp, basket_value_inr
            records.append(row.to_dict())
    return records


def zones_for_camera(store_config: dict, camera_id: str) -> dict:
    """Zone polygons that apply to one camera.

    A zone with a `camera_id` is only tested against that camera's frames
    (needed once several cameras / floors see different parts of the store).
    A zone without `camera_id` applies to every camera (legacy layout).
    """
    return {
        z["zone_id"]: z["polygon"]
        for z in store_config.get("zones", [])
        if z.get("camera_id") in (None, camera_id)
    }


def foot_to_plan(homography, fx: float, fy: float):
    """Project a normalised image point (person's feet) to floor-plan coords
    (0-1) with a 3x3 homography. Returns None when no calibration exists."""
    if not homography:
        return None
    H = np.array(homography, dtype=np.float64)
    v = H @ np.array([fx, fy, 1.0])
    if abs(v[2]) < 1e-9:
        return None
    return [round(float(v[0] / v[2]), 4), round(float(v[1] / v[2]), 4)]


def get_zone_for_point(cx: float, cy: float, zone_polygons: dict):
    for zone_id, polygon in zone_polygons.items():
        pts = np.array([[p[0], p[1]] for p in polygon], dtype=np.float32)
        result = cv2.pointPolygonTest(pts, (float(cx), float(cy)), False)
        if result >= 0:
            return zone_id
    return None


def process_single_clip(
    cap, model, tracker, staff_clf, deduplicator, pos_correlator,
    emitter, camera_id, store_id, zone_polygons, fps, clip_start,
    is_entry: bool = False,
    live: bool = False,
    lock=None,
    floor=None,
    homography=None,
):
    """live=True: `cap` is a stream.LiveStream already throttled to the target
    fps; timestamps come from the wall clock. `lock` serialises access to the
    shared tracker / classifier / emitter when several cameras run in threads."""
    import contextlib
    guard = lock if lock is not None else contextlib.nullcontext()
    dir_detector = DirectionDetector(tripwire_y=0.5, min_frames=5)
    frame_idx = 0
    PROCESS_EVERY_N = 3  # Process every 3rd frame for speed vs accuracy

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frame_idx += 1
        if not live and frame_idx % PROCESS_EVERY_N != 0:
            continue

        if live:
            frame_time = datetime.now(timezone.utc)
        else:
            offset_seconds = frame_idx / fps
            frame_time = clip_start + timedelta(seconds=offset_seconds)
        timestamp = frame_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        h, w = frame.shape[:2]

        try:
            results = model.track(
                frame,
                persist=True,
                classes=[0],
                conf=0.35,
                iou=0.4,
                tracker="bytetrack.yaml",
                verbose=False
            )
        except Exception as e:
            print(f"[WARN] Model inference error frame {frame_idx}: {e}")
            continue

        if results[0].boxes is None or results[0].boxes.id is None:
            continue

        with guard:
            boxes = results[0].boxes

            for box, track_id, conf in zip(
                boxes.xyxyn.cpu().numpy(),
                boxes.id.cpu().numpy().astype(int),
                boxes.conf.cpu().numpy()
            ):
                track_id = int(track_id)
                bbox_norm = box.tolist()

                if deduplicator.is_duplicate(track_id, camera_id, frame_time):
                    continue

                x1 = int(box[0] * w)
                y1 = int(box[1] * h)
                x2 = int(box[2] * w)
                y2 = int(box[3] * h)
                person_crop = frame[max(0, y1):y2, max(0, x1):x2]

                is_staff = staff_clf.classify(person_crop, bbox_norm, track_id=track_id)

                visitor_id, event_types = tracker.update(
                    track_id=track_id,
                    bbox=bbox_norm,
                    camera_id=camera_id,
                    timestamp=frame_time,
                    is_entry_camera=is_entry
                )

                # Direction detection — only on entry camera
                if is_entry:
                    direction = dir_detector.update(track_id, bbox_norm, frame_time)
                    if direction == "EXIT":
                        tracker.register_exit(track_id, frame_time, camera_id)
                        if "EXIT" not in event_types:
                            event_types.append("EXIT")

                cx = (box[0] + box[2]) / 2
                cy = (box[1] + box[3]) / 2
                current_zone = get_zone_for_point(cx, cy, zone_polygons)

                # Update zone transitions
                zone_events = tracker.update_zone(track_id, current_zone, frame_time)
                event_types.extend(zone_events)

                # Billing queue logic
                if current_zone == "BILLING":
                    queue_depth = tracker.get_billing_queue_depth(camera_id, frame_time)
                    if "ZONE_ENTER" in zone_events:
                        if queue_depth > 0:
                            event_types.append("BILLING_QUEUE_JOIN")
                            tracker.add_to_billing_queue(camera_id, visitor_id)
                    elif "ZONE_EXIT" in zone_events:
                        # Check if they abandoned (no POS transaction in 5 min)
                        if not pos_correlator.has_transaction_in_window(
                            zone_id=current_zone,
                            timestamp=frame_time,
                            window_minutes=5
                        ):
                            event_types.append("BILLING_QUEUE_ABANDON")
                        tracker.remove_from_billing_queue(camera_id, visitor_id)

                for event_type in event_types:
                    dwell_ms = 0
                    metadata = {
                        "queue_depth": None,
                        "sku_zone": current_zone,
                        "session_seq": tracker.get_session_seq(visitor_id)
                    }
                    if floor is not None:
                        metadata["floor"] = str(floor)
                    plan_xy = foot_to_plan(homography, float(cx), float(box[3]))
                    if plan_xy:
                        metadata["plan_x"], metadata["plan_y"] = plan_xy

                    if event_type == "ZONE_DWELL":
                        dwell_ms = tracker.get_dwell_ms(visitor_id, current_zone)

                    if event_type == "BILLING_QUEUE_JOIN":
                        metadata["queue_depth"] = tracker.get_billing_queue_depth(
                            camera_id, frame_time
                        )

                    emitter.emit({
                        "event_id": str(uuid.uuid4()),
                        "store_id": store_id,
                        "camera_id": camera_id,
                        "visitor_id": visitor_id,
                        "event_type": event_type,
                        "timestamp": timestamp,
                        "zone_id": current_zone if event_type not in ("ENTRY", "EXIT") else None,
                        "dwell_ms": dwell_ms,
                        "is_staff": is_staff,
                        "confidence": float(conf),
                        "metadata": metadata
                    })


def process_store_clips(
    store_id: str,
    clip_dir: Path,
    layout: dict,
    pos_data: list,
    output_path: Path
):
    """Process all 3 camera angles for one store."""
    try:
        from ultralytics import YOLO
        model = YOLO("yolov8n.pt")
    except Exception as e:
        print(f"[ERROR] Could not load YOLO model: {e}")
        print("[INFO] Make sure ultralytics is installed and yolov8n.pt is downloaded")
        return

    tracker = VisitorTracker(store_id=store_id)
    staff_clf = StaffClassifier()
    deduplicator = CrossCameraDeduplicator()
    pos_correlator = POSCorrelator(pos_data, store_id)
    emitter = EventEmitter(output_path)

    store_config = next(
        (s for s in layout["stores"] if s["store_id"] == store_id), None
    )
    if not store_config:
        raise ValueError(f"Store {store_id} not found in store_layout.json")

    camera_order = [c["camera_id"] for c in store_config.get("cameras", [])]
    _cam_map = {c["camera_id"]: c for c in store_config.get("cameras", [])}
    for camera_id in camera_order:
        cam_cfg = _cam_map.get(camera_id, {})
        filename = cam_cfg.get("filename", f"{camera_id}.mp4")
        clip_path = clip_dir / filename

        # Fallback dynamic matching if the file doesn't exist physically
        if not clip_path.exists():
            cam_type = cam_cfg.get("type", "")
            matched_file = None
            if clip_dir.exists():
                all_files = list(clip_dir.glob("*.mp4"))
                if cam_type == "entry_exit":
                    for f in all_files:
                        if "entry 1" in f.name.lower():
                            matched_file = f
                            break
                    if not matched_file:
                        for f in all_files:
                            if "entry" in f.name.lower():
                                matched_file = f
                                break
                elif cam_type == "floor":
                    for f in all_files:
                        if "zone" in f.name.lower():
                            matched_file = f
                            break
                    if not matched_file:
                        for f in all_files:
                            if "floor" in f.name.lower():
                                matched_file = f
                                break
                elif cam_type == "billing":
                    for f in all_files:
                        if "billing" in f.name.lower():
                            matched_file = f
                            break
                    if not matched_file:
                        for f in all_files:
                            if "cash" in f.name.lower():
                                matched_file = f
                                break
            if matched_file:
                clip_path = matched_file
                print(f"[INFO] Fallback matched camera {camera_id} (type={cam_type}) to clip file: {clip_path.name}")

        if not clip_path.exists():
            print(f"[WARN] Clip not found: {clip_path} (camera={camera_id}) — skipping")
            continue

        zone_polygons = zones_for_camera(store_config, camera_id)
        cap = cv2.VideoCapture(str(clip_path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
        store_open = store_config.get("open_time", "2026-04-10T06:30:00Z")
        clip_start = datetime.fromisoformat(store_open.replace("Z", "+00:00"))

        is_entry = (cam_cfg.get("type") == "entry_exit" or camera_id in ("CAM_ENTRY_01", "CAM_ENTRY_02"))
        print(f"[INFO] Processing {store_id}/{camera_id}  fps={fps:.1f} entry={is_entry}")
        
        process_single_clip(
            cap=cap, model=model, tracker=tracker, staff_clf=staff_clf,
            deduplicator=deduplicator, pos_correlator=pos_correlator,
            emitter=emitter, camera_id=camera_id, store_id=store_id,
            zone_polygons=zone_polygons, fps=fps, clip_start=clip_start,
            is_entry=is_entry,
            floor=cam_cfg.get("floor"),
            homography=cam_cfg.get("homography"),
        )
        cap.release()

    emitter.flush()
    print(f"[INFO] {store_id}: {emitter.total_events} events -> {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VisionRetail-IQ Store Intelligence Pipeline")
    parser.add_argument("--resources", default="resources",
                        help="Path to resources directory")
    parser.add_argument("--store", default=None,
                        help="Specific store ID (default: all stores in layout)")
    parser.add_argument("--output", default="events_output.jsonl",
                        help="Output JSONL file path")
    args = parser.parse_args()

    layout_path = f"{args.resources}/store_layout.json"
    pos_path = f"{args.resources}/pos_transactions.csv"

    if not Path(layout_path).exists():
        print(f"[ERROR] store_layout.json not found. Run: python pipeline/generate_layout.py")
        exit(1)

    layout = load_store_layout(layout_path)
    pos_data = load_pos_data(pos_path) if Path(pos_path).exists() else []
    output = Path(args.output)
    clips_dir = Path(args.resources) / "clips"

    stores = [args.store] if args.store else [s["store_id"] for s in layout["stores"]]
    for store_id in stores:
        # Resolve clip directory based on store ID
        if store_id == "store_2":
            store_clip_dir = clips_dir / "Store 2"
        elif store_id == "store_1":
            # Map Store 1 to either "Store 1" or "store_1"
            if (clips_dir / "Store 1").exists():
                store_clip_dir = clips_dir / "Store 1"
            else:
                store_clip_dir = clips_dir / "store_1"
        else:
            # Fallback
            store_config = next((s for s in layout["stores"] if s["store_id"] == store_id), None)
            name = store_config.get("name", "") if store_config else ""
            if "Store 2" in name:
                store_clip_dir = clips_dir / "Store 2"
            elif "Store 1" in name:
                store_clip_dir = clips_dir / "Store 1"
            else:
                store_clip_dir = clips_dir / store_id

        process_store_clips(
            store_id=store_id,
            clip_dir=store_clip_dir,
            layout=layout,
            pos_data=pos_data,
            output_path=output
        )
