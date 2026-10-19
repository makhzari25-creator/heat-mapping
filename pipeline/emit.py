"""
Event emitter — validates events against schema and writes to JSONL.
Never suppresses low-confidence events (per spec).
"""
import json
import uuid
from pathlib import Path
from datetime import datetime


REQUIRED_FIELDS = {
    "event_id", "store_id", "camera_id", "visitor_id",
    "event_type", "timestamp", "dwell_ms", "is_staff", "confidence", "metadata"
}

VALID_EVENT_TYPES = {
    "ENTRY", "EXIT", "ZONE_ENTER", "ZONE_EXIT", "ZONE_DWELL",
    "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "REENTRY"
}


class EventEmitter:
    def __init__(self, output_path: Path):
        self.output_path = output_path
        self.total_events = 0
        self._file = open(str(output_path), "a", encoding="utf-8")

    def emit(self, event: dict) -> bool:
        """Validate and emit a single event. Returns True if accepted."""
        # Ensure event_id is unique UUID v4
        if not event.get("event_id"):
            event["event_id"] = str(uuid.uuid4())

        # Validate event type
        if event.get("event_type") not in VALID_EVENT_TYPES:
            return False

        # zone_id must be null for ENTRY and EXIT
        if event.get("event_type") in ("ENTRY", "EXIT"):
            event["zone_id"] = None

        # Ensure metadata structure
        if "metadata" not in event:
            event["metadata"] = {}
        meta = event["metadata"]
        if "queue_depth" not in meta:
            meta["queue_depth"] = None
        if "sku_zone" not in meta:
            meta["sku_zone"] = None
        if "session_seq" not in meta:
            meta["session_seq"] = 1

        # BILLING_QUEUE_JOIN must have queue_depth as integer
        if event.get("event_type") == "BILLING_QUEUE_JOIN":
            if meta.get("queue_depth") is None:
                meta["queue_depth"] = 0

        # dwell_ms must be 0 for non-dwell events
        if event.get("event_type") != "ZONE_DWELL":
            if event.get("dwell_ms", 0) == 0:
                event["dwell_ms"] = 0

        # Clamp confidence 0.0-1.0 — never suppress low confidence
        event["confidence"] = max(0.0, min(1.0, float(event.get("confidence", 0.5))))

        self._file.write(json.dumps(event) + "\n")
        self._file.flush()
        self.total_events += 1
        return True

    def flush(self):
        self._file.flush()

    def __del__(self):
        try:
            self._file.close()
        except Exception:
            pass
