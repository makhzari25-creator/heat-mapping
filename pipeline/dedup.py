"""
Cross-camera deduplication.
Prevents the same physical person (same track_id) from generating duplicate
events when seen simultaneously by overlapping cameras (CAM_ENTRY_01 and CAM_FLOOR_01
overlap at the Brigade store entrance).
"""
from datetime import datetime, timedelta
from typing import Dict, Tuple


class CrossCameraDeduplicator:
    def __init__(self, window_seconds: int = 3):
        """
        window_seconds: time window within which the same track_id from a different
        camera is considered a duplicate.
        """
        self.window_seconds = window_seconds
        # (track_id, camera_id) -> last seen timestamp
        self._seen: Dict[Tuple[int, str], datetime] = {}

    def is_duplicate(
        self,
        track_id: int,
        camera_id: str,
        timestamp: datetime
    ) -> bool:
        """
        Returns True if this track_id was recently seen on a DIFFERENT camera.
        The primary camera (CAM_ENTRY_01) always wins — CAM_FLOOR_01 defers.
        """
        key = (track_id, camera_id)
        self._seen[key] = timestamp

        # Check if same track_id appeared on another camera within window
        cutoff = timestamp - timedelta(seconds=self.window_seconds)
        for (tid, cam), ts in list(self._seen.items()):
            if ts < cutoff:
                # Clean up stale entries
                del self._seen[(tid, cam)]
                continue
            if tid == track_id and cam != camera_id:
                # Seen on another camera recently
                # Entry camera is authoritative; suppress from secondary cameras
                if "entry" not in camera_id.lower():
                    return True

        return False

    def clear_track(self, track_id: int):
        keys_to_remove = [k for k in self._seen if k[0] == track_id]
        for k in keys_to_remove:
            del self._seen[k]
