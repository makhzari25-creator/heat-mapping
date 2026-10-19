"""
Staff vs Customer classifier using HSV uniform detection.

Brigade Bangalore context:
  - 5 named staff: kasthuri v, Zufishan Khazra, Shashikala, Naziya Begum, Priya v
  - Staff wear Purplle store uniforms — inspect actual footage to calibrate HSV ranges
  - Classification cache by track_id avoids reprocessing every frame

Design documented in CHOICES.md: HSV colour-based approach chosen over
trained classifier (no labelled data) and movement-based heuristic (too slow to detect
at entry point where staff classification matters most).
"""
import cv2
import numpy as np
from typing import Dict, Optional


class StaffClassifier:
    def __init__(self):
        # HSV ranges for Purplle staff uniforms
        # IMPORTANT: Inspect real footage frames to calibrate these
        # These defaults cover typical retail uniform colours (dark navy + white + purple)
        self.staff_hsv_ranges = [
            # Dark navy/blue uniform
            ((100, 50, 20), (130, 255, 100)),
            # White shirt / lab coat
            ((0, 0, 200), (180, 30, 255)),
            # Purplle brand purple (approx)
            ((130, 50, 50), (160, 255, 200)),
            # Magenta-violet (Purplle logo colour)
            ((140, 60, 60), (170, 255, 220)),
        ]
        # Cache: track_id -> (is_staff, frame_count)
        self.classification_cache: Dict[int, tuple] = {}
        # Re-classify every N frames to handle edge cases
        self.RECLASSIFY_EVERY = 30

    def classify(
        self,
        person_crop: np.ndarray,
        bbox_norm: list,
        track_id: Optional[int] = None
    ) -> bool:
        """
        Returns True if the person is identified as staff.
        Uses HSV colour matching on the torso region.
        """
        # Check cache first
        if track_id is not None:
            cached = self.classification_cache.get(track_id)
            if cached:
                is_staff, frame_count = cached
                if frame_count < self.RECLASSIFY_EVERY:
                    self.classification_cache[track_id] = (is_staff, frame_count + 1)
                    return is_staff

        result = self._classify_from_crop(person_crop)

        if track_id is not None:
            self.classification_cache[track_id] = (result, 0)

        return result

    def _classify_from_crop(self, person_crop: np.ndarray) -> bool:
        if person_crop is None or person_crop.size == 0:
            return False

        h, w = person_crop.shape[:2]
        if h < 20 or w < 10:
            return False

        # Focus on torso region (25%-65% of height)
        torso = person_crop[int(h * 0.25):int(h * 0.65), :]
        if torso.size == 0:
            return False

        try:
            hsv = cv2.cvtColor(torso, cv2.COLOR_BGR2HSV)
        except cv2.error:
            return False

        for lower, upper in self.staff_hsv_ranges:
            mask = cv2.inRange(
                hsv,
                np.array(lower, dtype=np.uint8),
                np.array(upper, dtype=np.uint8)
            )
            coverage = np.count_nonzero(mask) / mask.size
            if coverage > 0.35:
                return True

        return False

    def clear_cache(self, track_id: int):
        self.classification_cache.pop(track_id, None)
