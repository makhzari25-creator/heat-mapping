"""
Zone heatmap — normalised 0-100 visit frequency per zone.
Low confidence flag when < 20 sessions in zone.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from database import get_db
from models import HeatmapResponse, ZoneHeatmapItem
from datetime import date

router = APIRouter()


def _layout_zones(store_id: str):
    """Zones (id, name, floor) for a store from store_layout.json.
    Falls back to the built-in ZONE_NAMES when the layout has no zones."""
    import json
    from config_router import CONFIG_PATH
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            layout = json.load(f)
        store = next((s for s in layout.get("stores", []) if s.get("store_id") == store_id), None)
        zones = [(z["zone_id"], z.get("name", z["zone_id"]), z.get("floor"))
                 for z in (store or {}).get("zones", [])]
        if zones:
            return zones
    except Exception:
        pass
    return [(zid, name, None) for zid, name in ZONE_NAMES.items()]

ZONE_NAMES = {
    "MAKEUP": "Makeup & Colour",
    "SKINCARE": "Skincare & Treatments",
    "BATH_BODY": "Bath & Body",
    "HAIR": "Hair Care",
    "FRAGRANCE": "Fragrance",
    "PERSONAL_CARE": "Personal Care",
    "BILLING": "Billing Counter",
}


@router.get("/stores/{store_id}/heatmap", response_model=HeatmapResponse)
async def get_heatmap(
    store_id: str,
    date_str: str = None,
    floor: str = None,
    db: AsyncSession = Depends(get_db)
):
    target_date = date_str or date.today().isoformat()
    t_start = f"{target_date}T00:00:00"
    t_end = f"{target_date}T23:59:59.999Z"

    # Fetch all events for the store and date to do single python-based aggregation
    result = await db.execute(text("""
        SELECT event_type, zone_id, visitor_id, dwell_ms, metadata
        FROM events
        WHERE store_id = :sid AND is_staff = 0
          AND timestamp >= :t_start AND timestamp <= :t_end
    """), {"sid": store_id, "t_start": t_start, "t_end": t_end})
    events = result.fetchall()

    visitor_zones = {}  # visitor_id -> set of zones
    visitor_purchase = {}  # visitor_id -> amount
    visitor_abandon = set()  # visitor_ids who abandoned
    zone_dwells = {}  # zone_id -> list of dwell_ms
    unique_visitors = set()

    import json
    for etype, zid, vid, dwell, meta_str in events:
        if etype == "ENTRY":
            unique_visitors.add(vid)
        if vid not in visitor_zones:
            visitor_zones[vid] = set()
        if zid:
            visitor_zones[vid].add(zid)
            
        if etype == "BILLING_QUEUE_ABANDON":
            visitor_abandon.add(vid)
            
        if etype == "ZONE_EXIT" and zid == "BILLING" and meta_str:
            try:
                meta = json.loads(meta_str) if isinstance(meta_str, str) else meta_str
                if meta and "amount" in meta and meta["amount"] is not None:
                    visitor_purchase[vid] = float(meta["amount"])
            except Exception:
                pass
                
        if etype == "ZONE_DWELL" and zid and dwell:
            if zid not in zone_dwells:
                zone_dwells[zid] = []
            zone_dwells[zid].append(dwell)

    from models import DataConfidence

    sample_size = len(unique_visitors)
    if sample_size >= 20:
        conf_level = "HIGH"
        conf_score = 0.95
    elif sample_size >= 10:
        conf_level = "MEDIUM"
        conf_score = 0.80
    else:
        conf_level = "LOW"
        conf_score = 0.45

    confidence_data = DataConfidence(
        confidence_score=conf_score,
        confidence_level=conf_level,
        supporting_sample_size=sample_size
    )

    # Compute max visits for normalised visit frequency calculation
    layout_zones = _layout_zones(store_id)
    all_floors = sorted({str(f) for _, _, f in layout_zones if f is not None})
    if floor is not None:
        layout_zones = [z for z in layout_zones if str(z[2]) == str(floor)]
    zone_names = {zid: name for zid, name, _ in layout_zones}
    zone_floor = {zid: (str(f) if f is not None else None) for zid, _, f in layout_zones}

    zone_visits = {}
    for zid in zone_names.keys():
        visits = sum(1 for vid, zones in visitor_zones.items() if zid in zones)
        zone_visits[zid] = visits

    max_visits = max(zone_visits.values()) if zone_visits else 1  # normalised per floor view
    if max_visits == 0:
        max_visits = 1

    zones = []
    for zid, zname in zone_names.items():
        visits = zone_visits[zid]
        normalised = round((visits / max_visits) * 100, 1)
        dwells = zone_dwells.get(zid, [])
        avg_dwell = (sum(dwells) / len(dwells)) / 60000.0 if dwells else 0.0
        
        # Calculate conversions: visitors who visited this zone and had a billing purchase
        conversions = 0
        revenue = 0.0
        abandons = 0
        for vid, zset in visitor_zones.items():
            if zid in zset:
                if vid in visitor_purchase:
                    conversions += 1
                    revenue += visitor_purchase[vid]
                if vid in visitor_abandon:
                    abandons += 1

        zones.append(ZoneHeatmapItem(
            zone_id=zid,
            zone_name=zname,
            visit_frequency=normalised,
            avg_dwell_minutes=round(avg_dwell, 2),
            data_confidence="high" if visits >= 20 else "low",
            visits=visits,
            conversions=conversions,
            abandonment=abandons,
            revenue_influence=round(revenue, 2),
            floor=zone_floor.get(zid)
        ))

    from config import get_store_info
    store_info = get_store_info(store_id)

    return HeatmapResponse(
        store_id=store_id,
        store_name=store_info.get("store_name"),
        store_location=store_info.get("store_location"),
        city=store_info.get("city"),
        zones=zones,
        data_confidence=confidence_data,
        floors=all_floors,
        floor=floor
    )
