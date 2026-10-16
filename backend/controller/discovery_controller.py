import asyncio
import json
import os
import threading
import time
from typing import Dict, Any, List, Optional
from fastapi import APIRouter
from pydantic import BaseModel

import state
from state import (
    current_robots,
    discovered_robots,
    get_discovered_robots_list,
    load_fleet_adapters_data,
    save_fleet_adapters_data,
    broadcast,
)
from controller.adapter_controller import append_adapter_log, get_adapter_logs, reload_fleet_adapter

router = APIRouter(tags=["Robot Discovery & Auto-Adoption"])

# ==========================================
# Pydantic Request Models
# ==========================================
class AdoptRequest(BaseModel):
    robot_id: str
    fleet_name: str
    action: str  # "create_new_fleet" or "adopt_to_existing"
    adapter_id: Optional[str] = None
    graph_idx: Optional[int] = 0
    parking_waypoint: Optional[str] = None
    charger_waypoint: Optional[str] = None

# ==========================================
# Background Broadcast & Maintenance Loop
# (Discovered robots synced via Fleet Adapter)
# ==========================================
async def discovery_watchdog_loop():
    disc_file = os.path.abspath(os.path.join(os.path.dirname(__file__), "../saved_maps/discovered_robots.json"))
    while True:
        await asyncio.sleep(1.5)

        # Synchronize from Fleet Adapter's saved cache if available
        if os.path.exists(disc_file):
            try:
                with open(disc_file, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                    if isinstance(cached, dict):
                        for k, v in cached.items():
                            if isinstance(v, dict):
                                state.discovered_robots[k] = v
                        state.sync_robot_specs_to_adapters(cached)
            except Exception:
                pass

        discovered_list = get_discovered_robots_list()
        if discovered_list:
            await broadcast({
                "type": "discovered_robots",
                "discovered": discovered_list,
            })
        is_rmf_active = (time.time() - getattr(state, "last_ros2_time", 0.0)) < 2.5
        if current_robots and not is_rmf_active:
            await broadcast({
                "type": "fleet_states",
                "robots": list(current_robots.values()),
            })


# ==========================================
# REST API Endpoints
# ==========================================
@router.get("/api/discovered-robots")
def get_discovered_robots():
    """Returns currently auto-discovered AMR robots (forwarded by Fleet Adapter)."""
    return {
        "status": "ok",
        "mqtt_connected": state.is_fa_mqtt_connected(),
        "count": len(discovered_robots),
        "discovered": get_discovered_robots_list(),
    }

@router.post("/api/discovered-robots/adopt")
async def adopt_discovered_robot(req: AdoptRequest):
    """Adopts a discovered robot: either creates a new Fleet Adapter or attaches to an existing one."""
    adapters = load_fleet_adapters_data()
    pending_logs = []

    # Find discovered robot data
    robot_key = f"{req.fleet_name}_{req.robot_id}"
    disc = discovered_robots.get(robot_key)
    specs = disc.get("specs", {}) if disc else {}
    def_park = req.parking_waypoint or (disc.get("default_parking") if disc else "parking_1") or "parking_1"
    def_charge = req.charger_waypoint or (disc.get("default_charger") if disc else "charger_1") or "charger_1"

    if req.action == "create_new_fleet":
        # Check if an adapter for this fleet already exists
        existing_adapter = next((a for a in adapters if a.get("fleet_name") == req.fleet_name), None)
        if existing_adapter:
            # Append robot to existing adapter instead
            if not any(r.get("name") == req.robot_id for r in existing_adapter.get("robots", [])):
                existing_adapter["robots"].append({
                    "name": req.robot_id,
                    "parking_waypoint": def_park,
                    "charger_waypoint": def_charge,
                })
                existing_adapter["updated_at"] = "已更新"
                pending_logs.append((existing_adapter["id"], f"[INFO] 車輛 '{req.robot_id}' 已自動納管至車隊 '{req.fleet_name}'"))
        else:
            # Create a brand new Fleet Adapter
            new_adapter_id = f"adapter_{req.fleet_name}_{int(time.time())}"
            new_adapter = {
                "id": new_adapter_id,
                "name": f"{req.fleet_name} EasyFullControl",
                "fleet_name": req.fleet_name,
                "adapter_type": "easy_full_control",
                "status": "online",
                "graph_idx": req.graph_idx or 0,
                "ros2_domain_id": 0,
                "linear_velocity": float(specs.get("max_linear_velocity", 1.2)),
                "angular_velocity": float(specs.get("max_angular_velocity", 1.0)),
                "robot_radius": float(specs.get("footprint_radius", 0.35)),
                "recharge_threshold": 20,
                "recharge_target": 90,
                "default_charger": def_charge,
                "default_parking": def_park,
                "robots": [
                    {
                        "name": req.robot_id,
                        "parking_waypoint": def_park,
                        "charger_waypoint": def_charge,
                    }
                ],
                "latency_ms": 10,
                "updated_at": "已儲存",
            }
            pending_logs.append((new_adapter_id, f"[INFO] 自動建立車隊 '{req.fleet_name}' 並成功納管車輛 '{req.robot_id}'"))
            adapters.append(new_adapter)

    elif req.action == "adopt_to_existing":
        target_adapter = None
        if req.adapter_id:
            target_adapter = next((a for a in adapters if a.get("id") == req.adapter_id), None)
        if not target_adapter:
            target_adapter = next((a for a in adapters if a.get("fleet_name") == req.fleet_name), None)

        if not target_adapter:
            return {"status": "error", "message": f"找不到可指派的 Fleet Adapter (id={req.adapter_id})"}

        # Add robot if not already present
        if not any(r.get("name") == req.robot_id for r in target_adapter.get("robots", [])):
            target_adapter["robots"].append({
                "name": req.robot_id,
                "parking_waypoint": def_park,
                "charger_waypoint": def_charge,
            })
            target_adapter["updated_at"] = "已更新"
            pending_logs.append((target_adapter["id"], f"[INFO] 車輛 '{req.robot_id}' 已成功納管"))

    await reload_fleet_adapter(lambda: save_fleet_adapters_data(adapters))
    for adapter_id, message in pending_logs:
        append_adapter_log(adapter_id, message)

    # Broadcast updates
    broadcast_adapters = []
    for a in adapters:
        item = dict(a)
        item["logs"] = get_adapter_logs(a.get("id", ""))
        broadcast_adapters.append(item)

    await broadcast({
        "type": "fleet_adapters_updated",
        "adapters": broadcast_adapters,
    })
    await broadcast({
        "type": "discovered_robots",
        "discovered": get_discovered_robots_list(),
    })

    return {
        "status": "success",
        "message": f"車輛 '{req.robot_id}' 已成功納管！",
        "discovered": get_discovered_robots_list(),
    }

@router.post("/api/discovered-robots/batch-adopt")
async def batch_adopt_discovered_robots():
    """One-click batch adopt: automatically creates or adopts all unassigned discovered AMR robots."""
    adapters = load_fleet_adapters_data()
    discovered_list = get_discovered_robots_list()
    unadopted = [r for r in discovered_list if not r.get("is_adopted")]

    if not unadopted:
        return {"status": "ok", "message": "目前沒有待納管的車輛", "adopted_count": 0}

    adopted_count = 0
    pending_logs = []
    # Group unadopted robots by fleet_name
    fleet_groups: Dict[str, List[Dict[str, Any]]] = {}
    for r in unadopted:
        fleet_groups.setdefault(r["fleet_name"], []).append(r)

    for fleet_name, robots in fleet_groups.items():
        existing_adapter = next((a for a in adapters if a.get("fleet_name") == fleet_name), None)
        if existing_adapter:
            # Append all unadopted robots to existing adapter
            for r in robots:
                r_id = r["robot_id"]
                if not any(item.get("name") == r_id for item in existing_adapter.get("robots", [])):
                    existing_adapter["robots"].append({
                        "name": r_id,
                        "parking_waypoint": r.get("default_parking") or existing_adapter.get("default_parking") or "parking_1",
                        "charger_waypoint": r.get("default_charger") or existing_adapter.get("default_charger") or "charger_1",
                    })
                    adopted_count += 1
            existing_adapter["updated_at"] = "已更新"
            pending_logs.append((existing_adapter["id"], f"[INFO] 批次自動納管 {len(robots)} 台車輛"))
        else:
            # Create a brand new adapter for this fleet
            first_r = robots[0]
            specs = first_r.get("specs", {})
            new_id = f"adapter_{fleet_name}_{int(time.time())}"
            new_adapter = {
                "id": new_id,
                "name": f"{fleet_name} EasyFullControl",
                "fleet_name": fleet_name,
                "adapter_type": "easy_full_control",
                "status": "online",
                "graph_idx": 0,
                "ros2_domain_id": 0,
                "linear_velocity": float(specs.get("max_linear_velocity", 1.2)),
                "angular_velocity": float(specs.get("max_angular_velocity", 1.0)),
                "robot_radius": float(specs.get("footprint_radius", 0.35)),
                "recharge_threshold": 20,
                "recharge_target": 90,
                "default_charger": first_r.get("default_charger") or "charger_1",
                "default_parking": first_r.get("default_parking") or "parking_1",
                "robots": [
                    {
                        "name": r["robot_id"],
                        "parking_waypoint": r.get("default_parking") or "parking_1",
                        "charger_waypoint": r.get("default_charger") or "charger_1",
                    }
                    for r in robots
                ],
                "latency_ms": 10,
                "updated_at": "已儲存",
            }
            pending_logs.append((new_id, f"[INFO] 自動建立車隊 '{fleet_name}' 並納管 {len(robots)} 台車輛"))
            adapters.append(new_adapter)
            adopted_count += len(robots)

    await reload_fleet_adapter(lambda: save_fleet_adapters_data(adapters))
    for adapter_id, message in pending_logs:
        append_adapter_log(adapter_id, message)

    # Broadcast updates
    broadcast_adapters = []
    for a in adapters:
        item = dict(a)
        item["logs"] = get_adapter_logs(a.get("id", ""))
        broadcast_adapters.append(item)

    await broadcast({
        "type": "fleet_adapters_updated",
        "adapters": broadcast_adapters,
    })
    await broadcast({
        "type": "discovered_robots",
        "discovered": get_discovered_robots_list(),
    })

    return {
        "status": "success",
        "adopted_count": adopted_count,
        "message": f"成功批次自動納管 {adopted_count} 台 AMR 車輛！",
    }
