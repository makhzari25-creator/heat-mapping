from datetime import datetime, timezone
import json
from database import AsyncSessionLocal
from sqlalchemy import text


async def log_action(user_role: str, action: str, resource: str, metadata: dict = None):
    """Log a user action to the audit_logs database table asynchronously."""
    try:
        meta_str = json.dumps(metadata or {})
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        async with AsyncSessionLocal() as db:
            await db.execute(text("""
                INSERT INTO audit_logs (timestamp, user_role, action, resource, metadata)
                VALUES (:ts, :role, :act, :res, :meta)
            """), {
                "ts": now_str,
                "role": user_role,
                "act": action,
                "res": resource,
                "meta": meta_str
            })
            await db.commit()
    except Exception:
        # Gracefully handle audit logs insertion glitches (ensure high service availability)
        pass


async def log_ai_recommendation(
    endpoint: str,
    store_id: str,
    recommendation: str,
    confidence_score: float,
    confidence_level: str,
    evidence_snapshot: list,
    inputs_snapshot: dict,
):
    """
    Log a full AI recommendation to the audit trail so that every output is
    reproducible and auditable. Future users can inspect exactly why a
    recommendation was generated and what data drove it.

    Stored as action: AI_RECOMMENDATION_GENERATED
    """
    metadata = {
        "store_id": store_id,
        "recommendation": recommendation,
        "confidence_score": round(confidence_score, 3),
        "confidence_level": confidence_level,
        "evidence_snapshot": evidence_snapshot[:5],   # store top 5 evidence items
        "inputs_snapshot": {
            k: v for k, v in inputs_snapshot.items()
            if k in (
                "total_gmv", "conversion_rate", "current_queue_depth",
                "unique_visitors", "abandonment_rate", "avg_basket_value",
                "active_visitors",
            )
        },
    }
    await log_action(
        user_role="AI_ENGINE",
        action="AI_RECOMMENDATION_GENERATED",
        resource=endpoint,
        metadata=metadata,
    )
