"""Tests for RTSP/live input, floor support and the ROI/calibration helpers."""
import json
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pipeline"))

import cv2  # noqa: E402
from stream import LiveStream, resolve_source, is_live_source, mask_source  # noqa: E402
from detect import zones_for_camera, foot_to_plan  # noqa: E402
import roi_tool  # noqa: E402

EXAMPLE = Path(__file__).parent.parent / "resources" / "store_layout.clothing.example.json"


# ── stream helpers ───────────────────────────────────────────────────────────
def test_resolve_source_expands_env(monkeypatch):
    monkeypatch.setenv("CAM_PASS", "s3cret")
    assert resolve_source("rtsp://admin:${CAM_PASS}@10.0.0.5/s") == "rtsp://admin:s3cret@10.0.0.5/s"
    assert resolve_source("0") == 0


def test_is_live_and_mask():
    assert is_live_source("rtsp://x/y") and is_live_source(0)
    assert not is_live_source("clip.mp4")
    assert "s3cret" not in mask_source("rtsp://admin:s3cret@10.0.0.5/s")


def test_livestream_unreachable_source_does_not_block_and_stops():
    stop = threading.Event()
    s = LiveStream("rtsp://127.0.0.1:1/none", process_fps=5, reconnect_min=0.1,
                   reconnect_max=0.2, stop_event=stop, name="t")
    t0 = time.time()
    threading.Timer(0.6, stop.set).start()
    ok, frame = s.read()  # must return (False, None) once stopped
    assert (ok, frame) == (False, None)
    s.release()
    assert time.time() - t0 < 5


def test_livestream_reads_file_throttled(tmp_path):
    path = str(tmp_path / "c.avi")
    w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"MJPG"), 30, (64, 48))
    for i in range(300):
        w.write(np.full((48, 64, 3), i % 255, np.uint8))
    w.release()
    stop = threading.Event()
    s = LiveStream(path, process_fps=20, stop_event=stop, name="f")
    got = 0
    t0 = time.time()
    while got < 3:
        ok, fr = s.read()
        assert ok and fr.shape == (48, 64, 3)
        got += 1
    assert time.time() - t0 >= 0.08  # 3 frames @20fps can't be instant
    s.release()


# ── per-camera zones / homography ────────────────────────────────────────────
def test_zones_for_camera_filters_by_camera():
    store = json.loads(EXAMPLE.read_text())["stores"][0]
    z = zones_for_camera(store, "CAM_FLOOR_02")
    assert set(z) == {"KIDS", "WOMEN_RACK_C", "ACCESSORIES"}
    legacy = {"zones": [{"zone_id": "A", "polygon": [[0, 0], [1, 0], [1, 1]]}]}
    assert "A" in zones_for_camera(legacy, "ANY_CAM")


def test_example_layout_is_two_floors_and_consistent():
    store = json.loads(EXAMPLE.read_text())["stores"][0]
    cams = {c["camera_id"]: c for c in store["cameras"]}
    assert {z["floor"] for z in store["zones"]} == {"1", "2"}
    for z in store["zones"]:
        assert z["camera_id"] in cams
        assert cams[z["camera_id"]]["floor"] == z["floor"]
        assert len(z["polygon"]) >= 3 and len(z["plan_polygon"]) >= 3
    assert any(z["zone_id"] == "FITTING_ROOM" for z in store["zones"])


def test_homography_roundtrip_and_foot_to_plan():
    img = [(0.1, 0.2), (0.9, 0.2), (0.95, 0.9), (0.05, 0.9)]
    plan = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    H = roi_tool.compute_homography(img, plan)
    for (ix, iy), (px, py) in zip(img, plan):
        assert roi_tool.project_point(H, ix, iy) == pytest.approx([px, py], abs=1e-3)
    assert foot_to_plan(H, 0.1, 0.2) == pytest.approx([0.0, 0.0], abs=1e-3)
    assert foot_to_plan(None, 0.5, 0.5) is None
    with pytest.raises(ValueError):
        roi_tool.compute_homography(img[:3], plan[:3])


def test_roi_upsert_and_recalibrate(tmp_path):
    layout = json.loads(EXAMPLE.read_text())
    sid = "clothing_1"
    poly = [(0.1, 0.1), (0.5, 0.1), (0.5, 0.5), (0.1, 0.5)]
    z = roi_tool.upsert_zone(layout, sid, "CAM_FLOOR_01", "1", "NEW_ZONE", "New", poly)
    assert z["plan_polygon"] == z["polygon"]  # no calibration yet
    n = len(layout["stores"][0]["zones"])
    roi_tool.upsert_zone(layout, sid, "CAM_FLOOR_01", "1", "NEW_ZONE", "Renamed", poly)
    assert len(layout["stores"][0]["zones"]) == n  # replaced, not duplicated

    H = roi_tool.compute_homography(
        [(0, 0), (1, 0), (1, 1), (0, 1)], [(0, 0), (0.5, 0), (0.5, 0.5), (0, 0.5)])
    roi_tool.set_homography(layout, sid, "CAM_FLOOR_01", H)
    zone = next(z for z in layout["stores"][0]["zones"] if z["zone_id"] == "NEW_ZONE")
    assert zone["plan_polygon"][2] == pytest.approx([0.25, 0.25], abs=1e-3)

    out = tmp_path / "l.json"
    roi_tool.save_layout(layout, out)
    assert json.loads(out.read_text())["stores"][0]["cameras"][1]["homography"]


# ── backend: floor-aware heatmap ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_heatmap_floor_filter(monkeypatch):
    import heatmap
    import config_router
    from database import AsyncSessionLocal
    from sqlalchemy import text

    monkeypatch.setattr(config_router, "CONFIG_PATH", EXAMPLE)
    async with AsyncSessionLocal() as db:
        for i, (zid, cam) in enumerate([("NEW_ARRIVALS", "CAM_FLOOR_01"), ("KIDS", "CAM_FLOOR_02")]):
            await db.execute(text("""
                INSERT INTO events (event_id, store_id, camera_id, visitor_id, event_type, timestamp,
                                    zone_id, dwell_ms, is_staff, confidence, metadata)
                VALUES (:e, 'clothing_1', :c, :v, 'ZONE_ENTER', '2026-03-01T10:00:00Z', :z, 0, 0, 0.9, '{}')
            """), {"e": f"floor-test-{i}", "c": cam, "v": f"VIS_F{i}", "z": zid})
        await db.commit()
        all_ = await heatmap.get_heatmap("clothing_1", date_str="2026-03-01", floor=None, db=db)
        f2 = await heatmap.get_heatmap("clothing_1", date_str="2026-03-01", floor="2", db=db)

    assert all_.floors == ["1", "2"]
    assert {z.floor for z in all_.zones} == {"1", "2"}
    assert {z.zone_id for z in f2.zones} == {"KIDS", "WOMEN_RACK_C", "ACCESSORIES", "FITTING_ROOM"}
    assert next(z for z in f2.zones if z.zone_id == "KIDS").visits == 1
    assert next(z for z in f2.zones if z.zone_id == "FITTING_ROOM").visits == 0


# ── end-to-end: process_single_clip in live mode with a fake detector ───────
def test_process_single_clip_live_adds_floor_and_plan_xy(tmp_path):
    from datetime import datetime, timezone
    from detect import process_single_clip
    from tracker import VisitorTracker
    from staff_classifier import StaffClassifier
    from dedup import CrossCameraDeduplicator
    from pos_correlator import POSCorrelator
    from emit import EventEmitter

    class A:
        def __init__(s, a): s.a = np.array(a)
        def cpu(s): return s
        def numpy(s): return s.a

    class Boxes:
        xyxyn, id, conf = A([[0.1, 0.1, 0.3, 0.6]]), A([1.0]), A([0.9])

    class Model:
        def track(self, *a, **k):
            return [type("R", (), {"boxes": Boxes()})()]

    class Cap:
        n = 20
        def read(self):
            self.n -= 1
            return self.n >= 0, np.zeros((48, 64, 3), np.uint8)

    store = json.loads(EXAMPLE.read_text())["stores"][0]
    out = tmp_path / "e.jsonl"
    process_single_clip(
        Cap(), Model(), VisitorTracker("clothing_1"), StaffClassifier(), CrossCameraDeduplicator(),
        POSCorrelator([], "clothing_1"), EventEmitter(out), "CAM_FLOOR_01", "clothing_1",
        zones_for_camera(store, "CAM_FLOOR_01"), 15.0, datetime.now(timezone.utc),
        live=True, lock=threading.Lock(), floor="1",
        homography=[[1, 0, 0], [0, 1, 0], [0, 0, 1]])
    ev = [json.loads(l) for l in out.read_text().splitlines()]
    assert ev and ev[0]["zone_id"] == "NEW_ARRIVALS"
    m = ev[0]["metadata"]
    assert m["floor"] == "1" and m["plan_x"] == pytest.approx(0.2) and m["plan_y"] == pytest.approx(0.6)
