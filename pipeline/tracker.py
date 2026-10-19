"""
Visitor tracking with Re-ID for cross-session recognition.

Design decisions documented in CHOICES.md:
- Re-ID uses bounding-box height similarity (rejects OSNet — too slow, face-blur kills accuracy)
- Re-entry window: 30 minutes (typical retail browse-return behaviour)
- ZONE_DWELL emitted every 30s (spec requirement, not post-hoc batch computation)
"""
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional


class VisitorTracker:
    def __init__(self, store_id: str):
        self.store_id = store_id
        # track_id -> session dict
        self.active: Dict[int, dict] = {}
        # visitor_id -> closed session dict
        self.closed: Dict[str, dict] = {}
        # (visitor_id, zone_id) -> last dwell emit time
        self.dwell_emits: Dict[Tuple, datetime] = {}
        # visitor_id -> session sequence counter
        self.session_seqs: Dict[str, int] = {}
        # camera_id -> list of visitor_ids in billing queue
        self.billing_queue: Dict[str, List[str]] = {}
        
        # Deduplication/Re-ID Diagnostics & Statistics
        self.reid_stats = {
            "total_tracks": 0,
            "unified_cross_camera": 0,
            "total_confidence": 0.0,
            "reentries_detected": 0
        }

    def _check_cross_camera_match(
        self,
        bbox: list,
        camera_id: str,
        timestamp: datetime
    ) -> Tuple[Optional[str], float]:
        """
        Check if a track on a secondary camera matches an existing visitor session.
        Returns (visitor_id, confidence_score) if matched, else (None, 0.0).
        """
        curr_h = bbox[3] - bbox[1] if len(bbox) >= 4 else 0
        best_vid = None
        best_score = 0.0

        # Scan active sessions (tracks currently being updated)
        for tid, session in self.active.items():
            prev_bbox = session.get("last_bbox")
            if not prev_bbox:
                continue
            prev_h = prev_bbox[3] - prev_bbox[1]
            h_diff = abs(prev_h - curr_h)
            
            # Check time proximity: within 5 minutes (300 seconds)
            t_diff = abs((timestamp - session["last_seen"]).total_seconds())
            if t_diff < 300:
                h_score = max(0.0, 1.0 - (h_diff / 0.10)) if h_diff < 0.10 else 0.0
                t_score = max(0.0, 1.0 - (t_diff / 300.0))
                score = 0.7 * h_score + 0.3 * t_score
                if score > best_score and score > 0.5:
                    best_score = score
                    best_vid = session["visitor_id"]

        # Scan closed/completed sessions
        for visitor_id, cs in self.closed.items():
            prev_bbox = cs.get("last_bbox")
            if not prev_bbox:
                continue
            prev_h = prev_bbox[3] - prev_bbox[1]
            h_diff = abs(prev_h - curr_h)
            
            # Check proximity to exit_time: within 15 minutes (900 seconds)
            exit_t = cs["exit_time"]
            t_diff = abs((timestamp - exit_t).total_seconds())
            if t_diff < 900:
                h_score = max(0.0, 1.0 - (h_diff / 0.10)) if h_diff < 0.10 else 0.0
                t_score = max(0.0, 1.0 - (t_diff / 900.0))
                score = 0.7 * h_score + 0.3 * t_score
                if score > best_score and score > 0.5:
                    best_score = score
                    best_vid = visitor_id

        return best_vid, best_score

    def update(
        self,
        track_id: int,
        bbox: list,
        camera_id: str,
        timestamp: datetime,
        is_entry_camera: bool
    ) -> Tuple[str, List[str]]:
        """
        Update tracker with a new detection.
        Returns (visitor_id, list_of_event_types_to_emit).
        """
        events_to_emit = []
        self.reid_stats["total_tracks"] += 1

        if track_id in self.active:
            session = self.active[track_id]
            visitor_id = session["visitor_id"]
            session["last_seen"] = timestamp
            session["last_bbox"] = bbox

            # Check for zone dwell (30s intervals)
            zone_key = (visitor_id, session.get("current_zone"))
            if zone_key[1]:
                last_dwell = self.dwell_emits.get(zone_key)
                if last_dwell is None:
                    self.dwell_emits[zone_key] = timestamp
                elif (timestamp - last_dwell).total_seconds() >= 30:
                    events_to_emit.append("ZONE_DWELL")
                    self.dwell_emits[zone_key] = timestamp
        else:
            # New track_id — check if it's a cross-camera match or re-entry
            visitor_id = None
            reid_conf = 1.0

            if not is_entry_camera:
                visitor_id, reid_conf = self._check_cross_camera_match(bbox, camera_id, timestamp)
                if visitor_id:
                    self.reid_stats["unified_cross_camera"] += 1
                    self.reid_stats["total_confidence"] += reid_conf

            if not visitor_id:
                # Check for re-entry on entry camera (or fallback)
                visitor_id = self._check_reentry(bbox, camera_id, timestamp, is_entry_camera)
                if visitor_id:
                    events_to_emit.append("REENTRY")
                    self.reid_stats["reentries_detected"] += 1
                else:
                    visitor_id = self._new_visitor_id()
                    if is_entry_camera:
                        events_to_emit.append("ENTRY")

            self.active[track_id] = {
                "visitor_id": visitor_id,
                "entry_time": timestamp,
                "current_zone": None,
                "last_seen": timestamp,
                "last_bbox": bbox,
                "zone_enter_times": {},
                "reid_confidence": reid_conf
            }
            self.session_seqs.setdefault(visitor_id, 0)

        self.session_seqs[visitor_id] = self.session_seqs.get(visitor_id, 0) + 1
        return visitor_id, events_to_emit

    def update_zone(
        self,
        track_id: int,
        new_zone: Optional[str],
        timestamp: datetime
    ) -> List[str]:
        """
        Update zone for a tracked visitor.
        Returns list of zone events to emit (ZONE_EXIT, ZONE_ENTER).
        """
        events = []
        if track_id not in self.active:
            return events

        session = self.active[track_id]
        prev_zone = session.get("current_zone")

        if prev_zone != new_zone:
            if prev_zone:
                events.append("ZONE_EXIT")
                session["zone_enter_times"].pop(prev_zone, None)
            if new_zone:
                events.append("ZONE_ENTER")
                session["zone_enter_times"][new_zone] = timestamp
                # Reset dwell timer for new zone
                visitor_id = session["visitor_id"]
                self.dwell_emits[(visitor_id, new_zone)] = timestamp
            session["current_zone"] = new_zone

        return events

    def register_exit(
        self,
        track_id: int,
        timestamp: datetime,
        camera_id: str
    ) -> Optional[str]:
        """Close an active session and store for re-entry detection."""
        if track_id not in self.active:
            return None
        session = self.active.pop(track_id)
        visitor_id = session["visitor_id"]
        self.closed[visitor_id] = {
            "exit_time": timestamp,
            "last_bbox": session["last_bbox"],
            "exit_camera": camera_id
        }
        return visitor_id

    def _check_reentry(
        self,
        bbox: list,
        camera_id: str,
        timestamp: datetime,
        is_entry_camera: bool
    ) -> Optional[str]:
        """
        Check if this detection matches a recently-exited visitor.
        Uses bounding-box height similarity within 30-minute window.
        """
        if not is_entry_camera:
            return None

        cutoff = timestamp - timedelta(minutes=30)
        stale = [
            vid for vid, cs in self.closed.items()
            if cs["exit_time"] < cutoff
        ]
        for vid in stale:
            del self.closed[vid]

        for visitor_id, cs in list(self.closed.items()):
            if cs.get("exit_camera") != camera_id:
                continue
            prev_h = cs["last_bbox"][3] - cs["last_bbox"][1]
            curr_h = bbox[3] - bbox[1]
            if abs(prev_h - curr_h) < 0.05:
                del self.closed[visitor_id]
                return visitor_id
        return None

    def _new_visitor_id(self) -> str:
        return "VIS_" + uuid.uuid4().hex[:6]

    def get_session_seq(self, visitor_id: str) -> int:
        return self.session_seqs.get(visitor_id, 1)

    def get_dwell_ms(self, visitor_id: str, zone_id: str) -> int:
        for session in self.active.values():
            if session["visitor_id"] == visitor_id:
                zone_enter = session.get("zone_enter_times", {}).get(zone_id)
                if zone_enter:
                    now = datetime.now(zone_enter.tzinfo) if zone_enter.tzinfo else datetime.utcnow()
                    return int((now - zone_enter).total_seconds() * 1000)
        return 30000  # Default 30s minimum for ZONE_DWELL

    def get_billing_queue_depth(self, camera_id: str, timestamp: datetime) -> int:
        return len(self.billing_queue.get(camera_id, []))

    def add_to_billing_queue(self, camera_id: str, visitor_id: str):
        if camera_id not in self.billing_queue:
            self.billing_queue[camera_id] = []
        if visitor_id not in self.billing_queue[camera_id]:
            self.billing_queue[camera_id].append(visitor_id)

    def remove_from_billing_queue(self, camera_id: str, visitor_id: str):
        if camera_id in self.billing_queue:
            self.billing_queue[camera_id] = [
                v for v in self.billing_queue[camera_id] if v != visitor_id
            ]
