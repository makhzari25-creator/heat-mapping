"""
Live multi-camera pipeline (RTSP) for a clothing store.

Every camera in resources/store_layout.json that has a `source` (rtsp:// URL,
webcam index, or file path) gets its own thread + its own YOLO model (the
ByteTrack state lives inside the model, so models must not be shared between
streams). Tracker / staff classifier / dedup / emitter are shared behind one
lock, so a visitor seen by two cameras is still one visitor.

    python pipeline/run_live.py --store store_1 --output events_live.jsonl \
        --post http://localhost:8000/events/ingest

Tuning for weak hardware: lower --fps (2-3 is enough for hit-mapping) or use
a smaller --imgsz in .env (YOLO_IMGSZ).
"""
import argparse
import json
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from detect import (  # noqa: E402
    load_store_layout, load_pos_data, process_single_clip, zones_for_camera,
)
from stream import LiveStream, mask_source  # noqa: E402
from tracker import VisitorTracker  # noqa: E402
from staff_classifier import StaffClassifier  # noqa: E402
from pos_correlator import POSCorrelator  # noqa: E402
from emit import EventEmitter  # noqa: E402
from dedup import CrossCameraDeduplicator  # noqa: E402


def _camera_worker(cam, store_cfg, store_id, shared, stop, fps, weights):
    camera_id = cam["camera_id"]
    try:
        from ultralytics import YOLO
        model = YOLO(weights)
    except Exception as e:  # pragma: no cover
        print(f"[ERROR] {camera_id}: cannot load YOLO: {e}")
        return

    stream = LiveStream(
        cam["source"],
        process_fps=cam.get("process_fps", fps),
        stop_event=stop,
        name=camera_id,
    )
    is_entry = cam.get("type") == "entry_exit"
    print(f"[INFO] {camera_id} floor={cam.get('floor')} source={mask_source(cam['source'])} "
          f"fps={cam.get('process_fps', fps)} entry={is_entry}")
    try:
        process_single_clip(
            cap=stream, model=model,
            tracker=shared["tracker"], staff_clf=shared["staff"],
            deduplicator=shared["dedup"], pos_correlator=shared["pos"],
            emitter=shared["emitter"], camera_id=camera_id, store_id=store_id,
            zone_polygons=zones_for_camera(store_cfg, camera_id),
            fps=cam.get("process_fps", fps), clip_start=datetime.now(timezone.utc),
            is_entry=is_entry, live=True, lock=shared["lock"],
            floor=cam.get("floor"), homography=cam.get("homography"),
        )
    except Exception as e:  # keep other cameras alive
        print(f"[ERROR] {camera_id}: worker crashed: {e}")
    finally:
        stream.release()


def _poster(path: Path, url: str, stop: threading.Event, every: float = 5.0):
    """Tail the JSONL file and POST new events to the API (<=500 per batch).
    The read offset only advances after a successful POST, so a network
    outage just delays delivery; the API de-duplicates by event_id."""
    import urllib.request
    pos = 0
    while not stop.wait(every):
        try:
            with open(path, "rb") as f:
                f.seek(pos)
                data = f.read()
            end = data.rfind(b"\n") + 1  # only complete lines
            if end == 0:
                continue
            events = [json.loads(l) for l in data[:end].splitlines() if l.strip()]
            for i in range(0, len(events), 500):
                req = urllib.request.Request(
                    url, data=json.dumps({"events": events[i:i + 500]}).encode(),
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=10).read()
            pos += end
        except Exception as e:
            print(f"[WARN] post failed ({e}); will retry")


def main():
    ap = argparse.ArgumentParser(description="Live RTSP hit-mapping pipeline")
    ap.add_argument("--resources", default="resources")
    ap.add_argument("--layout", default=None,
                    help="layout JSON (default: <resources>/store_layout.json)")
    ap.add_argument("--store", default=None)
    ap.add_argument("--output", default="events_live.jsonl")
    ap.add_argument("--fps", type=float, default=float(os.getenv("PROCESS_FPS", "3")),
                    help="frames/sec analysed per camera (default 3)")
    ap.add_argument("--weights", default=os.getenv("YOLO_WEIGHTS", "yolov8n.pt"))
    ap.add_argument("--post", default=None, help="API ingest URL to stream events to")
    ap.add_argument("--floor", default=None, help="only cameras on this floor")
    args = ap.parse_args()

    layout = load_store_layout(args.layout or f"{args.resources}/store_layout.json")
    store_id = args.store or layout["stores"][0]["store_id"]
    store_cfg = next(s for s in layout["stores"] if s["store_id"] == store_id)
    cams = [c for c in store_cfg["cameras"] if c.get("source")
            and (args.floor is None or str(c.get("floor")) == str(args.floor))]
    if not cams:
        sys.exit("No cameras with a `source` in store_layout.json "
                 "(add e.g. \"source\": \"rtsp://user:${CAM1_PASS}@192.168.1.20/stream1\")")

    pos_path = Path(args.resources) / "pos_transactions.csv"
    pos_data = load_pos_data(str(pos_path)) if pos_path.exists() else []
    out = Path(args.output)
    shared = {
        "tracker": VisitorTracker(store_id=store_id),
        "staff": StaffClassifier(),
        "dedup": CrossCameraDeduplicator(),
        "pos": POSCorrelator(pos_data, store_id),
        "emitter": EventEmitter(out),
        "lock": threading.Lock(),
    }

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    threads = [threading.Thread(
        target=_camera_worker, name=c["camera_id"], daemon=True,
        args=(c, store_cfg, store_id, shared, stop, args.fps, args.weights)) for c in cams]
    if args.post:
        threads.append(threading.Thread(target=_poster, args=(out, args.post, stop), daemon=True))
    for t in threads:
        t.start()
    print(f"[INFO] live pipeline running with {len(cams)} camera(s); Ctrl+C to stop")
    while not stop.is_set():
        time.sleep(1)
    for t in threads:
        t.join(timeout=5)
    shared["emitter"].flush()
    print(f"[INFO] stopped. {shared['emitter'].total_events} events -> {out}")


if __name__ == "__main__":
    main()
