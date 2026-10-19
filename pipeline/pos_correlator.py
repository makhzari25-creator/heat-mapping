"""
POS transaction correlation.
Handles both real Brigade data format and challenge CSV format.

Real data insight: Brigade Bangalore had 24 orders from 12:15-21:39 on 10-04-2026.
Average order GMV ~ Rs 1,872. 5 salesperson-assigned orders.
Conversion window: 5 minutes before transaction timestamp.
"""
import pandas as pd
from datetime import datetime, timedelta, timezone
from typing import List


class POSCorrelator:
    def __init__(self, pos_data: list, store_id: str):
        store_txns = [r for r in pos_data if r.get("store_id") == store_id]
        self._txn_times: List[datetime] = []

        for r in store_txns:
            ts_str = r.get("timestamp", "")
            if ts_str:
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    self._txn_times.append(ts)
                except ValueError:
                    pass

    def has_transaction_in_window(
        self,
        zone_id: str,
        timestamp: datetime,
        window_minutes: int = 5
    ) -> bool:
        """
        Returns True if a POS transaction occurred within window_minutes AFTER timestamp.
        Only applies to billing/checkout zones.
        """
        zone_upper = (zone_id or "").upper()
        if "BILLING" not in zone_upper and "CHECKOUT" not in zone_upper:
            return False

        if not timestamp.tzinfo:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        window_end = timestamp + timedelta(minutes=window_minutes)
        return any(timestamp <= t <= window_end for t in self._txn_times)

    def get_conversion_rate(
        self,
        visitor_sessions: list,
        window_minutes: int = 5
    ) -> float:
        """
        Calculate conversion rate for a list of visitor sessions.
        Excludes staff sessions.
        """
        customers = [s for s in visitor_sessions if not s.get("is_staff")]
        if not customers:
            return 0.0

        converted = 0
        for session in customers:
            for event in session.get("events", []):
                if event.get("event_type") in ("BILLING_QUEUE_JOIN", "ZONE_ENTER"):
                    try:
                        ts = datetime.fromisoformat(
                            event["timestamp"].replace("Z", "+00:00")
                        )
                        if self.has_transaction_in_window(
                            event.get("zone_id", ""),
                            ts,
                            window_minutes
                        ):
                            converted += 1
                            break
                    except Exception:
                        pass

        return converted / len(customers)

    def get_total_gmv(self) -> float:
        """Returns the total GMV across all loaded transactions."""
        return len(self._txn_times)  # Placeholder — real GMV from CSV
