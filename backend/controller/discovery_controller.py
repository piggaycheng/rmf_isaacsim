import asyncio
import json
import os
import threading
import time
from typing import Dict, Any, List, Optional
from fastapi import APIRouter
from pydantic import BaseModel
import paho.mqtt.client as mqtt

import state
from state import (
    current_robots,
    discovered_robots,
    get_discovered_robots_list,
    load_fleet_adapters_data,
    save_fleet_adapters_data,
    broadcast,
)
from controller.adapter_controller import append_adapter_log, get_adapter_logs

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
# MQTT Background Auto-Discovery Client
# ==========================================
mqtt_client: Optional[mqtt.Client] = None
discovery_connected: bool = False

def init_discovery_mqtt():
    global mqtt_client
    mqtt_host = os.environ.get("MQTT_HOST", "127.0.0.1")
    mqtt_port = int(os.environ.get("MQTT_PORT", 1883))

    client_id = f"rmf_web_studio_discovery_{int(time.time())}"
    client = mqtt.Client(client_id=client_id)

    def on_connect(c, userdata, flags, rc):
        global discovery_connected
        if rc == 0:
            discovery_connected = True
            print(f"[DISCOVERY] Connected to MQTT Broker at {mqtt_host}:{mqtt_port}")
            # Wildcard subscription across all fleets and robots
            client.subscribe("rmf/+/robot/+/register", qos=1)
            client.subscribe("rmf/+/robot/+/heartbeat", qos=0)
            client.subscribe("rmf/+/robot/+/status", qos=1)
            client.subscribe("rmf/+/robot/+/deregister", qos=1)
            print("[DISCOVERY] Subscribed to rmf/+/robot/+/(register, heartbeat, status, deregister)")
        else:
            discovery_connected = False
            print(f"[DISCOVERY] MQTT connection failed: rc={rc}")

    def on_disconnect(c, userdata, rc):
        global discovery_connected
        discovery_connected = False
        print(f"[DISCOVERY] MQTT disconnected: rc={rc}")

    def on_message(c, userdata, msg):
        try:
            tokens = msg.topic.split("/")
            if len(tokens) < 5:
                return
            fleet_name = tokens[1]
            robot_id = tokens[3]
            action = tokens[4]

            payload = {}
            if msg.payload:
                try:
                    payload = json.loads(msg.payload.decode("utf-8"))
                except Exception:
                    payload = {}

            robot_key = f"{fleet_name}_{robot_id}"
            now = time.time()

            existing = discovered_robots.get(robot_key, {
                "robot_id": robot_id,
                "fleet_name": fleet_name,
                "x": 0.0,
                "y": 0.0,
                "yaw": 0.0,
                "battery": 100.0,
                "status": "idle",
                "specs": {},
                "initial_location": {},
                "default_parking": "parking_1",
                "default_charger": "charger_1",
                "first_seen": now,
            })

            existing["last_seen"] = now
            existing["fleet_name"] = fleet_name
            existing["robot_id"] = robot_id

            if action == "register":
                init_loc = payload.get("initial_location", {})
                if init_loc:
                    existing["x"] = float(init_loc.get("x", existing["x"]))
                    existing["y"] = float(init_loc.get("y", existing["y"]))
                    existing["yaw"] = float(init_loc.get("yaw", existing["yaw"]))
                    existing["initial_location"] = init_loc

                if "specs" in payload:
                    specs = payload.get("specs", {})
                    existing["specs"] = specs
                    footprint = specs.get("footprint_radius")
                    max_lin = specs.get("max_linear_velocity")
                    max_ang = specs.get("max_angular_velocity")
                    if footprint or max_lin:
                        adapters = load_fleet_adapters_data()
                        updated = False
                        for a in adapters:
                            if a.get("fleet_name") == fleet_name:
                                if footprint and a.get("robot_radius") != footprint:
                                    a["robot_radius"] = float(footprint)
                                    updated = True
                                if max_lin and a.get("linear_velocity") != max_lin:
                                    a["linear_velocity"] = float(max_lin)
                                    updated = True
                                if max_ang and a.get("angular_velocity") != max_ang:
                                    a["angular_velocity"] = float(max_ang)
                                    updated = True
                        if updated:
                            save_fleet_adapters_data(adapters)
                if "default_parking" in payload:
                    existing["default_parking"] = payload.get("default_parking")
                if "default_charger" in payload:
                    existing["default_charger"] = payload.get("default_charger")
                existing["status"] = "registering"

            elif action == "heartbeat":
                if "x" in payload:
                    existing["x"] = float(payload.get("x", existing["x"]))
                if "y" in payload:
                    existing["y"] = float(payload.get("y", existing["y"]))
                if "yaw" in payload:
                    existing["yaw"] = float(payload.get("yaw", existing["yaw"]))
                if "battery" in payload:
                    existing["battery"] = float(payload.get("battery", existing["battery"]))
                if "status" in payload:
                    existing["status"] = payload.get("status", existing["status"])
                if "level_name" in payload:
                    existing["level_name"] = payload.get("level_name")

            elif action == "deregister" or action == "status":
                if payload.get("status") == "offline" or action == "deregister":
                    existing["status"] = "offline"
                    if robot_id in current_robots:
                        del current_robots[robot_id]

            discovered_robots[robot_key] = existing

            # Synchronize active robots to current_robots for real-time map canvas rendering
            if existing.get("status") != "offline":
                prev_robot = current_robots.get(robot_id, {})
                has_rmf_task = bool(prev_robot.get("task_id"))

                current_robots[robot_id] = {
                    "id": robot_id,
                    "name": robot_id,
                    "fleet": fleet_name,
                    "x": round(float(existing["x"]), 3),
                    "y": round(float(existing["y"]), 3),
                    "yaw": round(float(existing["yaw"]), 3),
                    "battery": round(float(existing["battery"]), 1),
                    "status": "moving" if (existing.get("status") == "moving" or has_rmf_task) else ("charging" if existing.get("status") == "charging" else "idle"),
                    "current_task": prev_robot.get("current_task") if has_rmf_task else ("執行導航任務中" if existing.get("status") == "moving" else "在線待命中"),
                    "task_id": prev_robot.get("task_id", ""),
                }

        except Exception as e:
            print(f"[DISCOVERY] Error parsing MQTT message: {e}")

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    try:
        client.connect_async(mqtt_host, mqtt_port, keepalive=30)
        client.loop_start()
        mqtt_client = client
    except Exception as e:
        print(f"[DISCOVERY] Failed to start MQTT client: {e}")

# ==========================================
# Background Broadcast & Maintenance Loop
# ==========================================
async def discovery_watchdog_loop():
    init_discovery_mqtt()
    while True:
        await asyncio.sleep(1.5)
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
    """Returns currently auto-discovered AMR robots from EMQX MQTT."""
    return {
        "status": "ok",
        "mqtt_connected": discovery_connected,
        "count": len(discovered_robots),
        "discovered": get_discovered_robots_list(),
    }

@router.post("/api/discovered-robots/adopt")
async def adopt_discovered_robot(req: AdoptRequest):
    """Adopts a discovered robot: either creates a new Fleet Adapter or attaches to an existing one."""
    adapters = load_fleet_adapters_data()

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
                append_adapter_log(existing_adapter["id"], f"[INFO] 車輛 '{req.robot_id}' 已自動納管至車隊 '{req.fleet_name}'")
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
            append_adapter_log(new_adapter_id, f"[INFO] 自動建立車隊 '{req.fleet_name}' 並成功納管車輛 '{req.robot_id}'")
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
            append_adapter_log(target_adapter["id"], f"[INFO] 車輛 '{req.robot_id}' 已成功納管")

    save_fleet_adapters_data(adapters)

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
            append_adapter_log(existing_adapter["id"], f"[INFO] 批次自動納管 {len(robots)} 台車輛")
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
            append_adapter_log(new_id, f"[INFO] 自動建立車隊 '{fleet_name}' 並納管 {len(robots)} 台車輛")
            adapters.append(new_adapter)
            adopted_count += len(robots)

    save_fleet_adapters_data(adapters)

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
