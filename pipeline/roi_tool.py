"""
Click-based zone (ROI) + floor-plan calibration tool.  Run it on a computer
with a screen (not inside Docker).

    python pipeline/roi_tool.py --layout resources/store_layout.json \
        --store clothing_1 --camera CAM_FLOOR_01 \
        --source "rtsp://admin:PASS@192.168.1.102:554/stream1" \
        [--plan-image plans/floor1.png]

Keys in the camera window
    left click   add polygon point            right click / u  undo point
    Enter        finish polygon -> asks zone id + name in the terminal
    c            calibrate: click 4 floor corners in the camera view, then the
                 same 4 points on the floor-plan window (same order)
    d            delete last saved zone of this camera
    s            save to the layout file          q   quit

Everything is stored in 0-1 coordinates, so resolution changes don't matter:
  zone.polygon       -> where the zone is in the CAMERA image (used to classify)
  zone.plan_polygon  -> where it is on the FLOOR PLAN (used to draw the hit map;
                        projected automatically through the homography)
  camera.homography  -> 3x3 matrix image->plan (used to place each person's feet)
"""
import argparse
import json
from pathlib import Path

import numpy as np

PLAN_W, PLAN_H = 1000, 640  # floor-plan canvas used for the 0-1 conversion


# ── pure helpers (unit-tested, no GUI) ───────────────────────────────────────
def compute_homography(img_pts, plan_pts):
    """4 normalised image points + the 4 matching plan points -> 3x3 list."""
    import cv2
    if len(img_pts) != 4 or len(plan_pts) != 4:
        raise ValueError("need exactly 4 point pairs")
    H = cv2.getPerspectiveTransform(
        np.array(img_pts, dtype=np.float32), np.array(plan_pts, dtype=np.float32))
    return H.tolist()


def project_point(H, x, y):
    v = np.array(H, dtype=np.float64) @ np.array([x, y, 1.0])
    return [round(float(v[0] / v[2]), 4), round(float(v[1] / v[2]), 4)]


def project_polygon(H, polygon):
    return [project_point(H, x, y) for x, y in polygon]


def _store(layout, store_id):
    store = next((s for s in layout["stores"] if s["store_id"] == store_id), None)
    if store is None:
        raise KeyError(f"store {store_id!r} not in layout")
    return store


def upsert_zone(layout, store_id, camera_id, floor, zone_id, name, polygon, homography=None):
    """Add or replace a zone of one camera. Returns the stored zone dict."""
    store = _store(layout, store_id)
    zone = {
        "zone_id": zone_id, "name": name, "floor": str(floor) if floor is not None else None,
        "camera_id": camera_id,
        "polygon": [[round(x, 4), round(y, 4)] for x, y in polygon],
    }
    zone["plan_polygon"] = project_polygon(homography, polygon) if homography else zone["polygon"]
    zones = store.setdefault("zones", [])
    for i, z in enumerate(zones):
        if z["zone_id"] == zone_id:
            zones[i] = zone
            break
    else:
        zones.append(zone)
    return zone


def set_homography(layout, store_id, camera_id, homography):
    """Store H on the camera and re-project that camera's existing zones."""
    store = _store(layout, store_id)
    cam = next(c for c in store["cameras"] if c["camera_id"] == camera_id)
    cam["homography"] = homography
    for z in store.get("zones", []):
        if z.get("camera_id") == camera_id:
            z["plan_polygon"] = project_polygon(homography, z["polygon"])


def save_layout(layout, path):
    Path(path).write_text(json.dumps(layout, indent=2, ensure_ascii=False), encoding="utf-8")


# ── GUI ──────────────────────────────────────────────────────────────────────
def _grab_frame(source, plan_image=None):
    import cv2
    from stream import resolve_source
    if Path(str(source)).suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp"):
        frame = cv2.imread(str(source))
    else:
        cap = cv2.VideoCapture(resolve_source(source))
        frame = None
        for _ in range(30):  # skip a few frames: RTSP often starts grey
            ok, f = cap.read()
            if ok:
                frame = f
        cap.release()
    if frame is None:
        raise SystemExit(f"Could not read a frame from {source}")
    return frame


def _plan_canvas(plan_image):
    import cv2
    if plan_image and Path(plan_image).exists():
        img = cv2.resize(cv2.imread(str(plan_image)), (PLAN_W, PLAN_H))
    else:
        img = np.full((PLAN_H, PLAN_W, 3), 30, np.uint8)
        for i in range(0, PLAN_W, 100):
            cv2.line(img, (i, 0), (i, PLAN_H), (60, 60, 60), 1)
        for j in range(0, PLAN_H, 80):
            cv2.line(img, (0, j), (PLAN_W, j), (60, 60, 60), 1)
    return img


def _collect_points(win, base, n, color, label):
    """Block until the user clicks n points on window `win`; returns pixel pts."""
    import cv2
    pts = []

    def cb(event, x, y, *_):
        if event == cv2.EVENT_LBUTTONDOWN and len(pts) < n:
            pts.append((x, y))

    cv2.setMouseCallback(win, cb)
    while len(pts) < n:
        img = base.copy()
        for i, p in enumerate(pts):
            cv2.circle(img, p, 6, color, -1)
            cv2.putText(img, str(i + 1), (p[0] + 8, p[1] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
        cv2.putText(img, f"{label}: click point {len(pts) + 1}/{n}", (10, 28),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        cv2.imshow(win, img)
        if cv2.waitKey(30) == 27:
            return None
    return pts


def main():
    import cv2
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--layout", default="resources/store_layout.json")
    ap.add_argument("--store", required=True)
    ap.add_argument("--camera", required=True)
    ap.add_argument("--source", help="rtsp URL / video file / image (default: camera.source)")
    ap.add_argument("--plan-image", default=None)
    args = ap.parse_args()

    layout = json.loads(Path(args.layout).read_text(encoding="utf-8"))
    store = _store(layout, args.store)
    cam = next((c for c in store["cameras"] if c["camera_id"] == args.camera), None)
    if cam is None:
        raise SystemExit(f"camera {args.camera!r} not in layout")
    floor = cam.get("floor")
    frame = _grab_frame(args.source or cam.get("source") or cam.get("filename"))
    fh, fw = frame.shape[:2]
    win = f"{args.camera}  (Enter=finish zone  c=calibrate  s=save  q=quit)"
    cv2.namedWindow(win)
    current = []

    def on_mouse(event, x, y, *_):
        if event == cv2.EVENT_LBUTTONDOWN:
            current.append((x, y))
        elif event == cv2.EVENT_RBUTTONDOWN and current:
            current.pop()

    cv2.setMouseCallback(win, on_mouse)
    H = cam.get("homography")
    while True:
        img = frame.copy()
        for z in store.get("zones", []):
            if z.get("camera_id") == args.camera:
                pts = np.array([[int(x * fw), int(y * fh)] for x, y in z["polygon"]], np.int32)
                cv2.polylines(img, [pts], True, (80, 220, 80), 2)
                cv2.putText(img, z["zone_id"], tuple(pts[0]), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (80, 220, 80), 2)
        if current:
            cv2.polylines(img, [np.array(current, np.int32)], False, (0, 200, 255), 2)
            for p in current:
                cv2.circle(img, p, 5, (0, 200, 255), -1)
        cv2.putText(img, "calibrated" if H else "NOT calibrated (press c)", (10, 28),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0) if H else (0, 0, 255), 2)
        cv2.imshow(win, img)
        k = cv2.waitKey(30) & 0xFF
        if k in (ord("q"), 27):
            break
        if k == ord("u") and current:
            current.pop()
        elif k == 13 and len(current) >= 3:
            zid = input("zone_id (e.g. WOMEN_RACK_A): ").strip().upper().replace(" ", "_")
            name = input("display name: ").strip() or zid
            poly = [(x / fw, y / fh) for x, y in current]
            upsert_zone(layout, args.store, args.camera, floor, zid, name, poly, H)
            current.clear()
            print(f"  zone {zid} added (press s to save)")
        elif k == ord("d"):
            mine = [z for z in store["zones"] if z.get("camera_id") == args.camera]
            if mine:
                store["zones"].remove(mine[-1])
                print(f"  removed {mine[-1]['zone_id']}")
        elif k == ord("c"):
            cv2.setMouseCallback(win, lambda *a: None)
            a = _collect_points(win, frame, 4, (0, 200, 255), "CAMERA: 4 floor corners")
            plan_win = f"floor plan {floor}"
            cv2.namedWindow(plan_win)
            b = _collect_points(plan_win, _plan_canvas(args.plan_image), 4, (255, 120, 0),
                                "PLAN: same 4 points, same order") if a else None
            cv2.destroyWindow(plan_win)
            cv2.setMouseCallback(win, on_mouse)
            if a and b:
                H = compute_homography([(x / fw, y / fh) for x, y in a],
                                       [(x / PLAN_W, y / PLAN_H) for x, y in b])
                set_homography(layout, args.store, args.camera, H)
                print("  calibration done (press s to save)")
        elif k == ord("s"):
            save_layout(layout, args.layout)
            print(f"  saved -> {args.layout}")
    cv2.destroyAllWindows()


if __name__ == "__main__":
    import os
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    main()
