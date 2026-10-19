"""
Direction detector using virtual tripwire at Y=0.5 in normalised frame coordinates.
Emits ENTRY when person crosses downward, EXIT when crossing upward.
Requires min_frames of consistent direction to fire (debounce).
"""
from datetime import datetime
from typing import Dict, Optional


class DirectionDetector:
    def __init__(self, tripwire_y: float = 0.5, min_frames: int = 5):
        """
        tripwire_y: normalised Y position of the virtual line (0.0=top, 1.0=bottom)
        min_frames: number of consistent frames required to emit a direction event
        """
        self.tripwire_y = tripwire_y
        self.min_frames = min_frames

        # track_id -> {prev_cy, direction_frames, last_direction, crossed}
        self._tracks: Dict[int, dict] = {}

    def update(
        self,
        track_id: int,
        bbox_norm: list,
        timestamp: datetime
    ) -> Optional[str]:
        """
        Update with new bounding box.
        Returns "ENTRY", "EXIT", or None.
        bbox_norm: [x1, y1, x2, y2] normalised 0-1
        """
        cy = (bbox_norm[1] + bbox_norm[3]) / 2.0

        if track_id not in self._tracks:
            self._tracks[track_id] = {
                "prev_cy": cy,
                "direction_frames": 0,
                "pending_direction": None,
                "crossed": False
            }
            return None

        state = self._tracks[track_id]
        prev_cy = state["prev_cy"]
        delta = cy - prev_cy

        # Determine instantaneous direction
        if abs(delta) < 0.005:  # Dead zone — ignore micro-movements
            state["prev_cy"] = cy
            return None

        instant_dir = "down" if delta > 0 else "up"

        # Debounce: accumulate consistent direction frames
        if instant_dir == state.get("pending_direction"):
            state["direction_frames"] += 1
        else:
            state["pending_direction"] = instant_dir
            state["direction_frames"] = 1

        state["prev_cy"] = cy

        # Only fire crossing event once per tripwire cross
        tripwire_crossed = (
            (prev_cy < self.tripwire_y <= cy) or
            (prev_cy > self.tripwire_y >= cy)
        )

        if tripwire_crossed and state["direction_frames"] >= self.min_frames:
            if not state["crossed"]:
                state["crossed"] = True
                if instant_dir == "down":
                    return "ENTRY"
                else:
                    return "EXIT"
        elif not tripwire_crossed:
            state["crossed"] = False  # Reset for next crossing

        return None

    def remove_track(self, track_id: int):
        self._tracks.pop(track_id, None)
