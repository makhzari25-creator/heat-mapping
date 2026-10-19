"""
simulator.py — Real-time Shopper CV Event Simulator for store_1 and store_2.
"""

import asyncio
import random
import uuid
import json
from datetime import datetime, timezone
from typing import Dict, List, Optional
from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy import text
from pydantic import BaseModel

from database import AsyncSessionLocal
from utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/simulator", tags=["simulator"])

# Global simulator state
class SimulatorState:
    def __init__(self):
        self.active_visitors: Dict[str, dict] = {}  # visitor_id -> details
        self.queue_depth = {"store_1": 0, "store_2": 0}
        self.running = True
        
        # Presenter overrides
        self.trigger_queue_spike = {"store_1": False, "store_2": False}
        self.trigger_conversion_drop = {"store_1": False, "store_2": False}
        self.frozen_dead_zone: Dict[str, Optional[str]] = {"store_1": None, "store_2": None}

sim_state = SimulatorState()

ZONES = ["MAKEUP", "SKINCARE", "BATH_BODY", "HAIR", "FRAGRANCE", "PERSONAL_CARE"]

ZONE_DWELL_SECS = {
    "MAKEUP": (40, 180),
    "SKINCARE": (45, 200),
    "BATH_BODY": (30, 90),
    "HAIR": (30, 120),
    "FRAGRANCE": (25, 90),
    "PERSONAL_CARE": (20, 70),
    "BILLING": (60, 240),
}

# HTTP request models for controls
class ControlRequest(BaseModel):
    action: str  # "trigger_queue_spike" | "trigger_conversion_drop" | "freeze_zone" | "inject_visitor" | "reset"
    zone_id: Optional[str] = None
    store_id: Optional[str] = "store_1"

# Helper to format ISO UTC timestamps
def _ts_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _new_vid() -> str:
    return "VIS_" + uuid.uuid4().hex[:6]

def _get_cams(store_id: str):
    suffix = "_01" if store_id == "store_1" else "_02"
    return {
        "entry": f"CAM_ENTRY{suffix}",
        "floor": f"CAM_FLOOR{suffix}",
        "billing": f"CAM_BILLING{suffix}"
    }

async def _insert_event(event: dict):
    """Directly insert a single simulated event into the database."""
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("""
                INSERT INTO events (
                    event_id, store_id, camera_id, visitor_id, event_type,
                    timestamp, zone_id, dwell_ms, is_staff, confidence, metadata
                ) VALUES (
                    :event_id, :store_id, :camera_id, :visitor_id, :event_type,
                    :timestamp, :zone_id, :dwell_ms, :is_staff, :confidence, :metadata
                )
            """), {
                "event_id": event["event_id"],
                "store_id": event["store_id"],
                "camera_id": event["camera_id"],
                "visitor_id": event["visitor_id"],
                "event_type": event["event_type"],
                "timestamp": event["timestamp"],
                "zone_id": event.get("zone_id"),
                "dwell_ms": event.get("dwell_ms", 0),
                "is_staff": int(event.get("is_staff", False)),
                "confidence": round(event.get("confidence", 0.90), 3),
                "metadata": json.dumps(event.get("metadata", {}))
            })
            await db.commit()
    except Exception as e:
        logger.error(f"Simulator failed to insert event: {e}")

def _mk_event(event_type: str, visitor_id: str, camera_id: str, store_id: str, zone_id: Optional[str] = None, dwell_ms: int = 0, queue_depth: Optional[int] = None, sku_zone: Optional[str] = None, session_seq: int = 1, amount: Optional[float] = None, salesperson_id: Optional[int] = None) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": store_id,
        "camera_id": camera_id,
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": _ts_now(),
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": False,
        "confidence": random.uniform(0.85, 0.98),
        "metadata": {
            "queue_depth": queue_depth,
            "sku_zone": sku_zone,
            "session_seq": session_seq,
            "amount": amount,
            "salesperson_id": salesperson_id
        }
    }

async def spawn_visitor(store_id: str, visitor_id: Optional[str] = None):
    """Spawns a new visitor and emits an ENTRY event."""
    vid = visitor_id or _new_vid()
    cams = _get_cams(store_id)
    
    # Choose zone itinerary
    available_zones = [z for z in ZONES if z != sim_state.frozen_dead_zone.get(store_id)]
    if not available_zones:
        available_zones = ["MAKEUP"]
        
    num_zones = random.randint(1, 3)
    itinerary = random.sample(available_zones, min(num_zones, len(available_zones)))
    
    # Decide if shopper will purchase
    did_purchase = False if sim_state.trigger_conversion_drop.get(store_id) else (random.random() < 0.35)
    
    # Store visitor metadata (with dynamic coordinates)
    sim_state.active_visitors[vid] = {
        "store_id": store_id,
        "itinerary": itinerary,
        "current_zone": None,
        "state": "entered",  # "entered" | "browsing" | "billing" | "exiting"
        "dwell_remaining": 0,
        "dwell_accumulated": 0,
        "did_purchase": did_purchase,
        "seq": 1,
        "x": 0.05 + random.random() * 0.1,
        "y": 0.05 + random.random() * 0.1
    }
    
    # Emit ENTRY
    ev = _mk_event("ENTRY", vid, cams["entry"], store_id, session_seq=1)
    await _insert_event(ev)
    logger.info(f"Simulator: Customer {vid} entered store {store_id}")

async def run_simulator_step():
    """Runs a single logical cycle of the shopper simulation for both stores."""
    for store_id in ["store_1", "store_2"]:
        # 1. Spawning new visitors
        max_shoppers = 20 if sim_state.trigger_queue_spike.get(store_id, False) else 10
        spawn_chance = 0.50 if sim_state.trigger_queue_spike.get(store_id, False) else 0.20
        
        # Count active visitors for this store
        store_visitors = [vid for vid, v in sim_state.active_visitors.items() if v.get("store_id") == store_id]
        
        if len(store_visitors) < max_shoppers and random.random() < spawn_chance:
            await spawn_visitor(store_id)
            
        # 2. Update active visitors
        to_delete = []
        cams = _get_cams(store_id)
        
        for vid in store_visitors:
            details = sim_state.active_visitors.get(vid)
            if not details:
                continue
                
            details["dwell_accumulated"] += 4000  # 4 seconds step
            details["dwell_remaining"] = max(0, details["dwell_remaining"] - 4)
            
            details["seq"] += 1
            seq = details["seq"]
            
            # Wiggle coordinates slightly to simulate live moving tracking boxes on CCTV stream
            details["x"] = max(0.01, min(0.99, details["x"] + random.uniform(-0.02, 0.02)))
            details["y"] = max(0.01, min(0.99, details["y"] + random.uniform(-0.02, 0.02)))
            
            # State: Entered -> Move to first browsing zone
            if details["state"] == "entered":
                if details["itinerary"]:
                    next_z = details["itinerary"].pop(0)
                    details["current_zone"] = next_z
                    details["state"] = "browsing"
                    
                    # Zone coordinates map for realistic placement wiggling
                    zone_coords = {
                        "MAKEUP": (0.20, 0.25),
                        "SKINCARE": (0.60, 0.25),
                        "BATH_BODY": (0.15, 0.68),
                        "HAIR": (0.45, 0.68),
                        "FRAGRANCE": (0.70, 0.68),
                        "PERSONAL_CARE": (0.90, 0.42),
                    }
                    cx, cy = zone_coords.get(next_z, (0.5, 0.5))
                    details["x"] = cx + random.uniform(-0.08, 0.08)
                    details["y"] = cy + random.uniform(-0.08, 0.08)
                    
                    dmin, dmax = ZONE_DWELL_SECS.get(next_z, (30, 90))
                    if sim_state.trigger_queue_spike.get(store_id, False):
                        details["dwell_remaining"] = random.randint(10, 30)
                    else:
                        details["dwell_remaining"] = random.randint(dmin, dmax)
                        
                    ev = _mk_event("ZONE_ENTER", vid, cams["floor"], store_id, zone_id=next_z, session_seq=seq)
                    await _insert_event(ev)
                else:
                    details["state"] = "browsing"
                    details["dwell_remaining"] = 0
                    
            # State: Browsing
            elif details["state"] == "browsing":
                if details["dwell_remaining"] > 0:
                    if random.random() < 0.4:
                        ev = _mk_event("ZONE_DWELL", vid, cams["floor"], store_id, zone_id=details["current_zone"],
                                       dwell_ms=details["dwell_accumulated"], session_seq=seq)
                        await _insert_event(ev)
                else:
                    ev = _mk_event("ZONE_EXIT", vid, cams["floor"], store_id, zone_id=details["current_zone"], session_seq=seq)
                    await _insert_event(ev)
                    
                    if details["itinerary"]:
                        next_z = details["itinerary"].pop(0)
                        if next_z != sim_state.frozen_dead_zone.get(store_id):
                            details["current_zone"] = next_z
                            zone_coords = {
                                "MAKEUP": (0.20, 0.25),
                                "SKINCARE": (0.60, 0.25),
                                "BATH_BODY": (0.15, 0.68),
                                "HAIR": (0.45, 0.68),
                                "FRAGRANCE": (0.70, 0.68),
                                "PERSONAL_CARE": (0.90, 0.42),
                            }
                            cx, cy = zone_coords.get(next_z, (0.5, 0.5))
                            details["x"] = cx + random.uniform(-0.08, 0.08)
                            details["y"] = cy + random.uniform(-0.08, 0.08)
                            
                            dmin, dmax = ZONE_DWELL_SECS.get(next_z, (30, 90))
                            details["dwell_remaining"] = random.randint(dmin, dmax)
                            details["dwell_accumulated"] = 0
                            
                            ev = _mk_event("ZONE_ENTER", vid, cams["floor"], store_id, zone_id=next_z, session_seq=seq + 1)
                            details["seq"] += 1
                            await _insert_event(ev)
                        else:
                            details["dwell_remaining"] = 0
                    else:
                        if details["did_purchase"] or sim_state.trigger_queue_spike.get(store_id, False):
                            details["state"] = "billing"
                            details["current_zone"] = "BILLING"
                            details["x"] = 0.2 + random.random() * 0.6
                            details["y"] = 0.92 + random.random() * 0.04
                            
                            if sim_state.trigger_queue_spike.get(store_id, False):
                                details["dwell_remaining"] = random.randint(180, 400)
                                sim_state.queue_depth[store_id] += 1
                            else:
                                details["dwell_remaining"] = random.randint(30, 120)
                                sim_state.queue_depth[store_id] += 1
                                
                            ev1 = _mk_event("ZONE_ENTER", vid, cams["billing"], store_id, zone_id="BILLING", session_seq=seq + 1)
                            details["seq"] += 1
                            await _insert_event(ev1)
                            
                            ev2 = _mk_event("BILLING_QUEUE_JOIN", vid, cams["billing"], store_id, zone_id="BILLING",
                                            queue_depth=sim_state.queue_depth[store_id], sku_zone="BILLING", session_seq=seq + 2)
                            details["seq"] += 1
                            await _insert_event(ev2)
                        else:
                            if random.random() < 0.15 and not sim_state.trigger_conversion_drop.get(store_id, False):
                                details["state"] = "billing_abandon"
                                details["current_zone"] = "BILLING"
                                details["x"] = 0.2 + random.random() * 0.6
                                details["y"] = 0.92 + random.random() * 0.04
                                details["dwell_remaining"] = random.randint(20, 60)
                                sim_state.queue_depth[store_id] += 1
                                
                                ev1 = _mk_event("ZONE_ENTER", vid, cams["billing"], store_id, zone_id="BILLING", session_seq=seq + 1)
                                details["seq"] += 1
                                await _insert_event(ev1)
                                
                                ev2 = _mk_event("BILLING_QUEUE_JOIN", vid, cams["billing"], store_id, zone_id="BILLING",
                                                queue_depth=sim_state.queue_depth[store_id], sku_zone="BILLING", session_seq=seq + 2)
                                details["seq"] += 1
                                await _insert_event(ev2)
                            else:
                                details["state"] = "exiting"
                                details["dwell_remaining"] = 0
                                
            # State: Billing
            elif details["state"] in ("billing", "billing_abandon"):
                if details["dwell_remaining"] > 0:
                    if random.random() < 0.3:
                        ev = _mk_event("ZONE_DWELL", vid, cams["billing"], store_id, zone_id="BILLING",
                                       dwell_ms=details["dwell_accumulated"], session_seq=seq)
                        await _insert_event(ev)
                else:
                    sim_state.queue_depth[store_id] = max(0, sim_state.queue_depth[store_id] - 1)
                    
                    amount = None
                    salesperson_id = None
                    if details["state"] == "billing":
                        if store_id == "store_2":
                            salesperson_id = random.choice([2001, 2002, 2003, 2004, 2005])
                            amount = round(random.uniform(400, 5500), 2)
                        else:
                            salesperson_id = random.choice([1178, 971, 523, 737, 1190])
                            amount = round(random.uniform(350, 4800), 2)
                    
                    ev = _mk_event("ZONE_EXIT", vid, cams["billing"], store_id, zone_id="BILLING",
                                   session_seq=seq, amount=amount, salesperson_id=salesperson_id)
                    await _insert_event(ev)
                    
                    if details["state"] == "billing_abandon":
                        ev_abandon = _mk_event("BILLING_QUEUE_ABANDON", vid, cams["billing"], store_id, zone_id="BILLING", session_seq=seq + 1)
                        details["seq"] += 1
                        await _insert_event(ev_abandon)
                        
                    details["state"] = "exiting"
                    details["dwell_remaining"] = 0
                    
            # State: Exiting Store
            elif details["state"] == "exiting":
                details["x"] = 0.05 + random.random() * 0.1
                details["y"] = 0.05 + random.random() * 0.1
                ev = _mk_event("EXIT", vid, cams["entry"], store_id, session_seq=seq)
                await _insert_event(ev)
                logger.info(f"Simulator: Customer {vid} exited store {store_id}")
                to_delete.append(vid)
                
        for vid in to_delete:
            sim_state.active_visitors.pop(vid, None)

async def run_live_simulator():
    """Background simulator task loop."""
    logger.info("Simulator: Real-time CV simulator started")
    await asyncio.sleep(5)  # Let FastAPI startup and seed first
    
    while sim_state.running:
        try:
            await run_simulator_step()
        except Exception as e:
            logger.error(f"Simulator error in loop step: {e}", exc_info=True)
        await asyncio.sleep(4)  # Step every 4 seconds


# API Router routes
@router.get("/status")
async def get_simulator_status(store_id: str = "store_1"):
    """Returns the current state of the simulator for the specified store."""
    store_visitors = [v for v in sim_state.active_visitors.values() if v.get("store_id") == store_id]
    return {
        "running": sim_state.running,
        "active_visitors_count": len(store_visitors),
        "queue_depth": sim_state.queue_depth.get(store_id, 0),
        "overrides": {
            "trigger_queue_spike": sim_state.trigger_queue_spike.get(store_id, False),
            "trigger_conversion_drop": sim_state.trigger_conversion_drop.get(store_id, False),
            "frozen_dead_zone": sim_state.frozen_dead_zone.get(store_id),
        }
    }

@router.get("/active")
async def get_active_visitors(store_id: str = "store_1"):
    """Returns a list of currently active visitors and their real-time coordinates filtered by store."""
    active = []
    for vid, details in sim_state.active_visitors.items():
        if details.get("store_id") == store_id:
            active.append({
                "visitor_id": vid,
                "current_zone": details.get("current_zone"),
                "state": details.get("state"),
                "dwell_accumulated": details.get("dwell_accumulated", 0),
                "x": round(details.get("x", 0.5), 3),
                "y": round(details.get("y", 0.5), 3),
                "did_purchase": details.get("did_purchase", False)
            })
    return {"active_visitors": active, "count": len(active)}

@router.post("/control")
async def post_simulator_control(req: ControlRequest):
    """Processes control actions (triggers overrides, resets, or spawns new visitors)."""
    action = req.action.lower()
    store_id = req.store_id or "store_1"
    
    if action == "trigger_queue_spike":
        sim_state.trigger_queue_spike[store_id] = True
        sim_state.queue_depth[store_id] = max(sim_state.queue_depth.get(store_id, 0), 6)
        logger.info(f"Simulator Override: QUEUE SPIKE activated for {store_id}")
        
    elif action == "trigger_conversion_drop":
        sim_state.trigger_conversion_drop[store_id] = True
        logger.info(f"Simulator Override: CONVERSION DROP activated for {store_id}")
        
    elif action == "freeze_zone":
        if not req.zone_id or req.zone_id not in ZONES:
            raise HTTPException(status_code=400, detail=f"Invalid zone_id. Choose from {ZONES}")
        sim_state.frozen_dead_zone[store_id] = req.zone_id.upper()
        logger.info(f"Simulator Override: DEAD ZONE activated for {req.zone_id} in {store_id}")
        
    elif action == "inject_visitor":
        vid = _new_vid()
        await spawn_visitor(store_id, visitor_id=vid)
        logger.info(f"Simulator Presenter Injection: Spawned customer {vid} in {store_id}")
        return {"status": "ok", "message": f"Injected customer {vid}"}
        
    elif action == "reset":
        sim_state.trigger_queue_spike[store_id] = False
        sim_state.trigger_conversion_drop[store_id] = False
        sim_state.frozen_dead_zone[store_id] = None
        sim_state.queue_depth[store_id] = 0
        
        # Remove active visitors for this store
        to_remove = [vid for vid, v in sim_state.active_visitors.items() if v.get("store_id") == store_id]
        for vid in to_remove:
            sim_state.active_visitors.pop(vid, None)
            
        logger.info(f"Simulator Control Reset triggered for {store_id}")
        
    return {
        "status": "ok",
        "store_id": store_id,
        "simulator_state": {
            "queue_depth": sim_state.queue_depth.get(store_id, 0),
            "overrides": {
                "trigger_queue_spike": sim_state.trigger_queue_spike.get(store_id, False),
                "trigger_conversion_drop": sim_state.trigger_conversion_drop.get(store_id, False),
                "frozen_dead_zone": sim_state.frozen_dead_zone.get(store_id),
            }
        }
    }
