"""
Live video source for the pipeline (RTSP / HTTP / webcam / file).

LiveStream keeps a background thread that always drains the camera, so the
consumer (YOLO) only ever sees the *newest* frame. Without this, a slow
detector makes OpenCV's internal buffer grow and the heatmap lags further and
further behind real time.

* Auto-reconnect with exponential backoff when the stream drops.
* Consumer-side frame throttling via `process_fps` (cheap on CPU).
* Same `read()` / `release()` / `get()` surface as cv2.VideoCapture, so
  detect.process_single_clip works with it unchanged.
"""
import os
import re
import threading
import time
from typing import Optional, Tuple

import cv2

# TCP is far more reliable than UDP on store Wi-Fi / busy LANs.
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    "rtsp_transport;tcp|stimeout;5000000",
)

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def resolve_source(source):
    """Expand ${ENV_VAR} placeholders so credentials stay out of the layout file.

    'rtsp://admin:${CAM1_PASS}@192.168.1.20:554/stream1'
    A purely numeric string means a local webcam index.
    """
    if isinstance(source, int):
        return source
    source = str(source)
    source = _ENV_RE.sub(lambda m: os.environ.get(m.group(1), ""), source)
    return int(source) if source.isdigit() else source


def is_live_source(source) -> bool:
    if isinstance(source, int):
        return True
    return str(source).lower().startswith(("rtsp://", "rtsps://", "http://", "https://", "rtmp://"))


def mask_source(source) -> str:
    """Hide the password when logging a URL."""
    return re.sub(r"://([^:/@]+):[^@]*@", r"://\1:***@", str(source))


class LiveStream:
    def __init__(
        self,
        source,
        process_fps: float = 5.0,
        reconnect_min: float = 1.0,
        reconnect_max: float = 30.0,
        stale_after: float = 15.0,
        stop_event: Optional[threading.Event] = None,
        name: str = "stream",
    ):
        self.source = resolve_source(source)
        self.process_fps = max(0.1, float(process_fps))
        self.reconnect_min = reconnect_min
        self.reconnect_max = reconnect_max
        self.stale_after = stale_after
        self.stop_event = stop_event or threading.Event()
        self.name = name

        self._cap: Optional[cv2.VideoCapture] = None
        self._lock = threading.Lock()
        self._frame = None
        self._frame_id = 0
        self._last_frame_ts = 0.0
        self._last_served_id = 0
        self._last_served_ts = 0.0
        self.fps = 15.0
        self.reconnects = 0
        self._thread = threading.Thread(target=self._reader, name=f"reader-{name}", daemon=True)
        self._thread.start()

    # ── background reader ────────────────────────────────────────────────
    def _open(self) -> bool:
        cap = cv2.VideoCapture(self.source)
        if not cap.isOpened():
            cap.release()
            return False
        self._cap = cap
        self.fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
        return True

    def _close(self):
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def _reader(self):
        delay = self.reconnect_min
        while not self.stop_event.is_set():
            if self._cap is None:
                if not self._open():
                    print(f"[WARN] {self.name}: cannot open {mask_source(self.source)}; retry in {delay:.0f}s")
                    if self.stop_event.wait(delay):
                        break
                    delay = min(delay * 2, self.reconnect_max)
                    continue
                if self.reconnects:
                    print(f"[INFO] {self.name}: reconnected")
                self.reconnects += 1
                delay = self.reconnect_min
                self._last_frame_ts = time.monotonic()

            ok, frame = self._cap.read()
            now = time.monotonic()
            if ok and frame is not None:
                with self._lock:
                    self._frame = frame
                    self._frame_id += 1
                    self._last_frame_ts = now
                continue

            # read failed or the stream froze -> drop and reconnect
            if (not ok) or (now - self._last_frame_ts > self.stale_after):
                print(f"[WARN] {self.name}: stream lost; reconnecting")
                self._close()
                if self.stop_event.wait(delay):
                    break
                delay = min(delay * 2, self.reconnect_max)
        self._close()

    # ── cv2.VideoCapture-compatible surface ──────────────────────────────
    def get(self, prop_id):
        if prop_id == cv2.CAP_PROP_FPS:
            return self.fps
        return 0.0

    def isOpened(self) -> bool:
        return not self.stop_event.is_set()

    def read(self) -> Tuple[bool, Optional["cv2.Mat"]]:
        """Block until a *new* frame is due (throttled to process_fps).
        Returns (False, None) only when stopped."""
        interval = 1.0 / self.process_fps
        while not self.stop_event.is_set():
            wait = self._last_served_ts + interval - time.monotonic()
            if wait > 0:
                if self.stop_event.wait(min(wait, 0.5)):
                    break
                continue
            with self._lock:
                if self._frame is not None and self._frame_id != self._last_served_id:
                    frame, self._last_served_id = self._frame, self._frame_id
                    self._last_served_ts = time.monotonic()
                    return True, frame.copy()
            if self.stop_event.wait(0.02):
                break
        return False, None

    def release(self):
        self.stop_event.set()
        self._thread.join(timeout=3)
        self._close()
