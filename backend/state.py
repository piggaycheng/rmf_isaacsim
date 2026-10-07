import os
import json
import asyncio
from typing import List, Dict, Any, Optional
from fastapi import WebSocket
from pydantic import BaseModel
from PIL import Image
import yaml

# ==========================================
# Paths & Storage Directories
# ==========================================
UPLOAD_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "uploads"))
os.makedirs(UPLOAD_DIR, exist_ok=True)

MAPS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "saved_maps"))
os.makedirs(MAPS_DIR, exist_ok=True)

NAV_GRAPHS_DIR = os.path.join(MAPS_DIR, "nav_graphs")
os.makedirs(NAV_GRAPHS_DIR, exist_ok=True)

ACTIVE_MAP_FILE = os.path.join(MAPS_DIR, "active_nav_graph.json")
ACTIVE_YAML_FILE = os.path.join(MAPS_DIR, "active_nav_graph.building.yaml")
ACTIVE_NAV_0_FILE = os.path.join(NAV_GRAPHS_DIR, "0.yaml")

SLAM_METADATA_FILE = os.path.join(UPLOAD_DIR, "slam_map_metadata.json")
SLAM_YAML_FILE = os.path.join(UPLOAD_DIR, "current_slam_map.yaml")
SLAM_PNG_FILE = os.path.join(UPLOAD_DIR, "current_slam_map.png")

FLEET_ADAPTERS_FILE = os.path.join(MAPS_DIR, "fleet_adapters.json")

# ==========================================
# Pydantic Request / Data Models
# ==========================================
class TaskRequest(BaseModel):
    type: str  # 'patrol', 'delivery', 'goto'
    target_waypoint: str
    destination_waypoint: Optional[str] = None
    robot_id: Optional[str] = None

class MapSaveRequest(BaseModel):
    name: str = "office_map"
    waypoints: List[Dict[str, Any]]
    lanes: List[Dict[str, Any]]
    graphs: Optional[List[Dict[str, Any]]] = None

class CameraDisableRequest(BaseModel):
    camera_name: str
    topic_prefix: Optional[str] = "slam/cameras"

# ==========================================
# In-Memory Global State
# ==========================================
DEFAULT_ROBOTS: Dict[str, Dict[str, Any]] = {}

current_robots: Dict[str, Dict[str, Any]] = dict(DEFAULT_ROBOTS)

current_tasks: List[Dict[str, Any]] = []

active_connections: List[WebSocket] = []
last_ros2_time: float = 0.0

async def broadcast(message: dict):
    for connection in list(active_connections):
        try:
            await connection.send_json(message)
        except Exception:
            pass

# ==========================================
# Helpers for SLAM Map
# ==========================================
def load_initial_slam_map() -> Dict[str, Any]:
    # 1. Try reading saved metadata JSON
    if os.path.exists(SLAM_METADATA_FILE) and os.path.exists(SLAM_PNG_FILE):
        try:
            with open(SLAM_METADATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and data.get("image_url"):
                    return data
        except Exception as e:
            print(f"Notice: Failed to load slam_map_metadata.json: {e}")

    # 2. If PNG image exists on disk, recover metadata from disk + yaml
    if os.path.exists(SLAM_PNG_FILE):
        try:
            img = Image.open(SLAM_PNG_FILE)
            w, h = img.size
            res = 0.05
            orig = [-10.0, -10.0, 0.0]
            if os.path.exists(SLAM_YAML_FILE):
                try:
                    with open(SLAM_YAML_FILE, "r", encoding="utf-8") as yf:
                        ydata = yaml.safe_load(yf)
                        if isinstance(ydata, dict):
                            res = float(ydata.get("resolution", 0.05))
                            orig = list(ydata.get("origin", [-10.0, -10.0, 0.0]))
                except Exception:
                    pass
            recovered = {
                "name": "current_slam_map.png",
                "image_url": "/api/map/image",
                "resolution": res,
                "origin": orig,
                "width": w,
                "height": h,
                "real_width_m": w * res,
                "real_height_m": h * res,
                "opacity": 0.85,
            }
            with open(SLAM_METADATA_FILE, "w", encoding="utf-8") as f:
                json.dump(recovered, f, indent=2, ensure_ascii=False)
            return recovered
        except Exception as e:
            print(f"Notice: Failed to recover SLAM map from PNG: {e}")
    return {}

current_slam_map: Dict[str, Any] = load_initial_slam_map()

# ==========================================
# Helpers for Fleet Adapters
# ==========================================
def load_fleet_adapters_data() -> List[Dict[str, Any]]:
    if os.path.exists(FLEET_ADAPTERS_FILE):
        try:
            with open(FLEET_ADAPTERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    # Ensure loaded data doesn't contain legacy logs
                    for item in data:
                        item.pop("logs", None)
                    return data
        except Exception as e:
            print(f"Notice: Failed to load fleet_adapters.json: {e}")
    return []

def save_fleet_adapters_data(adapters: List[Dict[str, Any]]) -> None:
    try:
        # Strip runtime logs before persisting configuration to disk
        clean_adapters = []
        for a in adapters:
            item = dict(a)
            item.pop("logs", None)
            clean_adapters.append(item)

        with open(FLEET_ADAPTERS_FILE, "w", encoding="utf-8") as f:
            json.dump(clean_adapters, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving fleet_adapters.json: {e}")
