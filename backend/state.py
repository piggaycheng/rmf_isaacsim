import os
import json
import asyncio
import math
from typing import List, Dict, Any, Optional, Tuple
from fastapi import WebSocket
from pydantic import BaseModel, field_validator
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
OFFLINE_RELOAD_FILE = os.path.join(MAPS_DIR, "offline_reload_pending.json")

# Mirrors OFF_GRAPH_THRESHOLD_M in mqtt_fleet_adapter.py, which decides off-graph; used in messages only.
OFF_GRAPH_THRESHOLD_M = 3.0

# ==========================================
# Pydantic Request / Data Models
# ==========================================
def validate_rounds(val: Any) -> Tuple[Optional[int], Optional[str]]:
    """
    Validates patrol rounds:
    - Must be a strict integer (rejects bool, str, float, null, 0, negative numbers, dicts, lists).
    - Must be > 0.
    Returns (validated_int, error_message). If invalid, (None, error_message).
    """
    if val is None:
        return None, "巡邏輪數 (rounds) 不能為 null，必須為正整數"
    if isinstance(val, bool) or not isinstance(val, int):
        return None, "巡邏輪數 (rounds) 必須為整數型別，不可為布林值、小數或字串"
    if val <= 0:
        return None, "巡邏輪數 (rounds) 必須為大於 0 的正整數"
    return val, None


class TaskRequest(BaseModel):
    type: str  # 'patrol', 'delivery', 'goto', 'multi_step'
    target_waypoint: Optional[str] = None
    destination_waypoint: Optional[str] = None
    robot_id: Optional[str] = None
    rounds: Optional[Any] = 1

    @field_validator("rounds", mode="before")
    @classmethod
    def check_rounds(cls, v):
        validated, err = validate_rounds(v)
        if err:
            raise ValueError(err)
        return validated

class MultiStepTaskRequest(BaseModel):
    robot_id: str
    fleet_name: Optional[str] = None
    steps: List[Dict[str, Any]]
    auto_proceed: bool = False
    step_delay: float = 0.0
    task_id: Optional[str] = None

class ComposeTaskRequest(BaseModel):
    """Multi-step task scheduled by Open-RMF (compose). robot_id omitted/'any' lets RMF choose."""
    steps: List[Dict[str, Any]]
    robot_id: Optional[str] = None
    fleet_name: Optional[str] = None
    task_id: Optional[str] = None

class StepProceedRequest(BaseModel):
    fleet_name: Optional[str] = None
    cmd_id: Optional[int] = None

class MapSaveRequest(BaseModel):
    name: str = "office_map"
    waypoints: List[Dict[str, Any]]
    lanes: List[Dict[str, Any]]
    graphs: Optional[List[Dict[str, Any]]] = None
    # Multi-floor data (see nav_graph_builder); None keeps the saved levels/lifts.
    levels: Optional[List[Dict[str, Any]]] = None
    lifts: Optional[List[Dict[str, Any]]] = None
    restart_adapter: Optional[bool] = False
    allow_offline_reload: bool = False

class CameraDisableRequest(BaseModel):
    camera_name: str
    topic_prefix: Optional[str] = "slam/cameras"

class CameraPtzRequest(BaseModel):
    camera_name: str
    action: str = "step"  # 'step', 'continuous', 'preset', 'home', 'stop'
    pan: float = 0.0      # -180.0 to 180.0 degrees
    tilt: float = 0.0     # -90.0 to 90.0 degrees
    zoom: float = 1.0     # 1.0 to 10.0
    step: float = 5.0     # step size
    speed: float = 1.0    # speed factor
    topic_prefix: Optional[str] = "slam/cameras"

# ==========================================
# In-Memory Global State
# ==========================================
DEFAULT_ROBOTS: Dict[str, Dict[str, Any]] = {}

current_robots: Dict[str, Dict[str, Any]] = dict(DEFAULT_ROBOTS)

current_tasks: List[Dict[str, Any]] = []

TERMINAL_STATES = {"completed", "failed", "canceled", "cancelled", "rejected"}
simulation_mode: bool = False
# Mirrored from the Fleet Adapter's /rmf/fleet_adapter_state; the FA owns paused_fleets.json.
paused_fleets: set = set()
estopped_robots: set = set()  # "{fleet}/{robot}", operator emergency stop (owned by the FA)
fa_state: Dict[str, Any] = {}
fa_state_time: float = 0.0
FA_STATE_STALE_S = 5.0
DISCOVERY_OFFLINE_S = 15.0

# Discovered Robots (Synced from Fleet Adapter)
discovered_robots: Dict[str, Dict[str, Any]] = {}

# Current Lifts (Synced from /lift_states or MockLift)
current_lifts: Dict[str, Dict[str, Any]] = {}

active_connections: List[WebSocket] = []
last_ros2_time: float = 0.0


def update_fa_state(data: Dict[str, Any]) -> None:
    global fa_state, fa_state_time
    import time
    fa_state = data
    fa_state_time = time.time()
    paused = data.get("paused_fleets")
    if isinstance(paused, list):
        paused_fleets.clear()
        paused_fleets.update(paused)
    estopped = data.get("estopped_robots")
    if isinstance(estopped, list):
        estopped_robots.clear()
        estopped_robots.update(estopped)


def is_fa_state_fresh() -> bool:
    import time
    return fa_state_time > 0 and (time.time() - fa_state_time) < FA_STATE_STALE_S


def is_robot_estopped(fleet_name: str, robot_id: str) -> bool:
    return f"{fleet_name}/{robot_id}" in estopped_robots


def find_robot_fleet(robot_id: str) -> Optional[str]:
    rob = current_robots.get(robot_id)
    if rob and rob.get("fleet"):
        return rob["fleet"]
    for disc in discovered_robots.values():
        if disc.get("robot_id") == robot_id and disc.get("fleet_name"):
            return disc["fleet_name"]
    return None


def is_fa_mqtt_connected() -> bool:
    return is_fa_state_fresh() and bool(fa_state.get("mqtt_connected"))


def is_robot_offline(fleet_name: str, robot_id: str) -> bool:
    """Robot link state as reported by the Fleet Adapter (heartbeat/LWT/deregister)."""
    import time
    disc = discovered_robots.get(f"{fleet_name}_{robot_id}")
    if not disc:
        return False
    return disc.get("status") == "offline" or (time.time() - disc.get("last_seen", 0)) > DISCOVERY_OFFLINE_S


def _point_to_segment_distance(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    import math
    abx = bx - ax
    aby = by - ay
    seg_len_sq = abx * abx + aby * aby
    if seg_len_sq < 1e-9:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * abx + (py - ay) * aby) / seg_len_sq))
    proj_x = ax + t * abx
    proj_y = ay + t * aby
    return math.hypot(px - proj_x, py - proj_y)


def compute_nav_graph_distance(fleet_name: str, x: float, y: float, level: Optional[str] = None) -> Optional[float]:
    """Computes shortest distance from (x, y) to active navigation graph or 0.yaml.

    With level, only that floor's waypoints count; JSON waypoints are in their floor's map frame.
    """
    import math
    if os.path.exists(ACTIVE_MAP_FILE):
        try:
            with open(ACTIVE_MAP_FILE, "r", encoding="utf-8") as mf:
                mdata = json.load(mf)
            wps = mdata.get("waypoints", [])
            if level:
                first = (mdata.get("levels") or [{}])[0].get("name")
                wps = [w for w in wps if (w.get("level") or first) in (level, None)]
            lanes = mdata.get("lanes", [])
            wp_map = {w.get("id"): (float(w.get("x", 0.0)), float(w.get("y", 0.0))) for w in wps if w.get("id")}
            min_dist = float("inf")
            for wx, wy in wp_map.values():
                d = math.hypot(x - wx, y - wy)
                if d < min_dist:
                    min_dist = d
            for l in lanes:
                sid, eid = l.get("start_id"), l.get("end_id")
                if sid in wp_map and eid in wp_map:
                    ax, ay = wp_map[sid]
                    bx, by = wp_map[eid]
                    d = _point_to_segment_distance(x, y, ax, ay, bx, by)
                    if d < min_dist:
                        min_dist = d
            if min_dist != float("inf"):
                return min_dist
        except Exception:
            pass

    for fname in ["0.yaml"]:
        ypath = os.path.join(NAV_GRAPHS_DIR, fname)
        if os.path.exists(ypath):
            try:
                with open(ypath, "r", encoding="utf-8") as yf:
                    yd = yaml.safe_load(yf) or {}
                for lvl in yd.get("levels", {}).values():
                    raw_v = lvl.get("vertices", [])
                    raw_l = lvl.get("lanes", [])
                    v_coords = [(float(v[0]), float(v[1])) for v in raw_v]
                    min_dist = float("inf")
                    for vx, vy in v_coords:
                        d = math.hypot(x - vx, y - vy)
                        if d < min_dist:
                            min_dist = d
                    for ln in raw_l:
                        if len(ln) >= 2 and 0 <= ln[0] < len(v_coords) and 0 <= ln[1] < len(v_coords):
                            (ax, ay), (bx, by) = v_coords[ln[0]], v_coords[ln[1]]
                            d = _point_to_segment_distance(x, y, ax, ay, bx, by)
                            if d < min_dist:
                                min_dist = d
                    if min_dist != float("inf"):
                        return min_dist
            except Exception:
                pass
    return None


def is_robot_off_graph(fleet_name: str, robot_id: str, x: Optional[float] = None, y: Optional[float] = None) -> Tuple[bool, float]:
    """Determines whether a robot is excessively off-graph (> OFF_GRAPH_THRESHOLD_M, decided by the fleet adapter).

    Returns: (is_off_graph: bool, distance: float)
    """
    off_robots = fa_state.get("off_graph_robots", {})
    if isinstance(off_robots, dict):
        key = f"{fleet_name}/{robot_id}"
        if key in off_robots:
            return True, float(off_robots[key].get("distance", 0.0))
        for k, v in off_robots.items():
            if isinstance(v, dict) and v.get("robot_id") == robot_id:
                return True, float(v.get("distance", 0.0))

    disc = discovered_robots.get(f"{fleet_name}_{robot_id}")
    if disc and "is_off_graph" in disc:
        return bool(disc.get("is_off_graph")), float(disc.get("off_graph_distance", 0.0))

    rob = current_robots.get(robot_id)
    if x is None or y is None:
        if rob:
            x, y = rob.get("x"), rob.get("y")
        elif disc:
            x, y = disc.get("x"), disc.get("y")
    level = next((r.get("level_name") or r.get("map_name") for r in (rob, disc)
                  if r and (r.get("level_name") or r.get("map_name"))), None)

    if x is not None and y is not None:
        dist = compute_nav_graph_distance(fleet_name, float(x), float(y), level)
        if dist is not None:
            return dist > 5.0, round(dist, 2)

    return False, 0.0


def nav_graph_file_sha(graph_idx: int) -> Optional[str]:
    import hashlib
    try:
        with open(os.path.join(NAV_GRAPHS_DIR, f"{int(graph_idx)}.yaml"), "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()
    except (OSError, ValueError, TypeError):
        return None


def loaded_fleet_waypoints() -> Optional[Dict[str, set]]:
    """Waypoints of each fleet's graph as actually loaded by the Fleet Adapter (None if not reported)."""
    if not is_fa_state_fresh():
        return None
    fleets = fa_state.get("fleets")
    if not isinstance(fleets, dict):
        return None
    return {name: set(info.get("waypoints") or []) for name, info in fleets.items() if isinstance(info, dict)}


def adapter_diagnostics(adapter: Dict[str, Any]) -> Dict[str, Any]:
    """Compares a configured adapter with what the running Fleet Adapter loaded.

    status: "ok" | "stale" (restart needed for the new graph/config to take effect) | "unknown".
    """
    fleet = adapter.get("fleet_name", "")
    graph_idx = adapter.get("graph_idx", 0)
    issues: List[str] = []
    warnings = [
        w.get("message", "")
        for w in fa_state.get("waypoint_warnings") or []
        if isinstance(w, dict) and str(w.get("key", "")).startswith(f"{fleet}_")
    ]
    fleets = fa_state.get("fleets")
    if not fa_state:
        return {"status": "unknown", "issues": ["尚未收到 Fleet Adapter 狀態（Adapter 可能未啟動）"], "warnings": warnings}
    if not is_fa_state_fresh():
        return {"status": "unknown", "issues": ["Fleet Adapter 狀態已過期，無法確認目前載入路網，請檢查服務連線"], "warnings": warnings}
    if not isinstance(fleets, dict):
        return {"status": "unknown", "issues": ["Fleet Adapter 未回報已載入路網，請重啟 Adapter 以啟用路網檢查"], "warnings": warnings}

    loaded = fleets.get(fleet)
    if not isinstance(loaded, dict):
        issues.append(f"車隊 '{fleet}' 尚未被 Fleet Adapter 載入，需重啟 Adapter")
    else:
        if loaded.get("graph_idx") != graph_idx:
            issues.append(f"Adapter 仍使用 Graph {loaded.get('graph_idx')}，設定為 Graph {graph_idx}，需重啟 Adapter")
        else:
            disk_sha = nav_graph_file_sha(graph_idx)
            if not disk_sha or not loaded.get("graph_sha"):
                return {"status": "unknown", "issues": [f"無法比對 Graph {graph_idx} 路網版本，請檢查路網檔與 Adapter 回報"], "warnings": warnings}
            if disk_sha != loaded.get("graph_sha"):
                issues.append(f"Graph {graph_idx} 路網檔已更新，但 Adapter 仍使用舊版路網，需重啟 Adapter")
    return {
        "status": "stale" if issues else "ok",
        "issues": issues,
        "warnings": warnings,
        "loaded_graph_idx": loaded.get("graph_idx") if isinstance(loaded, dict) else None,
        "loaded_waypoints": len(loaded.get("waypoints") or []) if isinstance(loaded, dict) else 0,
        "fa_state_fresh": is_fa_state_fresh(),
    }


def sync_robot_specs_to_adapters(discovered: Dict[str, Dict[str, Any]]) -> None:
    """Persist conservative limits from adopted robots, never unrelated discovery cache entries."""
    adapters = load_fleet_adapters_data()
    changed_fleets = []
    for adapter in adapters:
        fleet_name = adapter.get("fleet_name")
        names = {
            r.get("name") if isinstance(r, dict) else r
            for r in adapter.get("robots", [])
        }
        robots = [
            r for r in discovered.values()
            if isinstance(r, dict) and r.get("fleet_name") == fleet_name
            and r.get("robot_id") in names
        ]
        changed = False
        for field, spec_key in (
            ("robot_radius", "footprint_radius"),
            ("linear_velocity", "max_linear_velocity"),
            ("angular_velocity", "max_angular_velocity"),
        ):
            values = []
            for robot in robots:
                raw = (robot.get("specs") or {}).get(spec_key)
                if raw is None:
                    continue
                try:
                    value = float(raw)
                except (TypeError, ValueError):
                    print(f"[Discovery WARN] Invalid {spec_key} for {fleet_name}/{robot.get('robot_id')}: {raw!r}")
                    continue
                if not math.isfinite(value) or value <= 0:
                    print(f"[Discovery WARN] Invalid {spec_key} for {fleet_name}/{robot.get('robot_id')}: {raw!r}")
                    continue
                values.append(value)
            if not values:
                continue
            value = max(values) if field == "robot_radius" else min(values)
            if field != "robot_radius" and adapter.get(field):
                value = min(float(adapter[field]), value)
            if adapter.get(field) != value:
                adapter[field] = value
                changed = True
        if changed:
            changed_fleets.append(fleet_name)
    if changed_fleets:
        save_fleet_adapters_data(adapters)
        print(f"[Discovery] Updated fleet_adapters.json with adopted robot specs: {changed_fleets}")

async def broadcast(message: dict):
    for connection in list(active_connections):
        try:
            await connection.send_json(message)
        except Exception:
            pass

def get_discovered_robots_list() -> List[Dict[str, Any]]:
    import time
    adapters = load_fleet_adapters_data()
    adopted_map: Dict[str, Dict[str, Any]] = {}
    for a in adapters:
        for r in a.get("robots", []):
            r_name = r.get("name") if isinstance(r, dict) else str(r)
            if r_name:
                adopted_map[r_name] = {
                    "adapter_id": a.get("id"),
                    "adapter_name": a.get("name"),
                    "fleet_name": a.get("fleet_name"),
                }

    wall_now = time.time()
    result = []
    for key, rob in list(discovered_robots.items()):
        item = dict(rob)
        r_id = item.get("robot_id", "")
        if r_id in adopted_map:
            item["is_adopted"] = True
            item["adopted_info"] = adopted_map[r_id]
        else:
            item["is_adopted"] = False
            item["adopted_info"] = None

        last_seen = item.get("last_seen", 0)
        if item.get("status") == "offline" or wall_now - last_seen > DISCOVERY_OFFLINE_S:
            item["connection_status"] = "offline"
        else:
            item["connection_status"] = "online"

        dist = item.get("off_graph_distance")
        if isinstance(dist, (int, float)) and (math.isinf(dist) or math.isnan(dist)):
            item["off_graph_distance"] = 999.0

        result.append(item)
    return result

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

def load_all_slam_maps() -> Dict[str, Dict[str, Any]]:
    maps: Dict[str, Dict[str, Any]] = {}
    initial = load_initial_slam_map()
    if initial and isinstance(initial, dict) and initial.get("image_url"):
        maps["1F"] = dict(initial)
        maps["1F"]["level_name"] = "1F"

    if os.path.exists(UPLOAD_DIR):
        for fname in os.listdir(UPLOAD_DIR):
            if fname.startswith("slam_map_metadata_") and fname.endswith(".json"):
                lvl_name = fname[len("slam_map_metadata_"):-len(".json")]
                meta_path = os.path.join(UPLOAD_DIR, fname)
                png_path = os.path.join(UPLOAD_DIR, f"slam_map_{lvl_name}.png")
                if os.path.exists(png_path):
                    try:
                        with open(meta_path, "r", encoding="utf-8") as f:
                            mdata = json.load(f)
                            if isinstance(mdata, dict):
                                mdata["level_name"] = lvl_name
                                maps[lvl_name] = mdata
                    except Exception as e:
                        print(f"Notice: Failed to load {fname}: {e}")

        for fname in os.listdir(UPLOAD_DIR):
            if fname.startswith("slam_map_") and fname.endswith(".png") and not fname.startswith("slam_map_metadata"):
                lvl_name = fname[len("slam_map_"):-len(".png")]
                if lvl_name and lvl_name not in maps and lvl_name != "metadata":
                    png_path = os.path.join(UPLOAD_DIR, fname)
                    yaml_path = os.path.join(UPLOAD_DIR, f"slam_map_{lvl_name}.yaml")
                    try:
                        img = Image.open(png_path)
                        w, h = img.size
                        res = 0.05
                        orig = [-10.0, -10.0, 0.0]
                        if os.path.exists(yaml_path):
                            try:
                                with open(yaml_path, "r", encoding="utf-8") as yf:
                                    yd = yaml.safe_load(yf)
                                    if isinstance(yd, dict):
                                        res = float(yd.get("resolution", 0.05))
                                        orig = list(yd.get("origin", [-10.0, -10.0, 0.0]))
                            except Exception:
                                pass
                        recovered = {
                            "name": fname,
                            "image_url": f"/api/map/image/{lvl_name}",
                            "resolution": res,
                            "origin": orig,
                            "width": w,
                            "height": h,
                            "real_width_m": w * res,
                            "real_height_m": h * res,
                            "opacity": 0.85,
                            "level_name": lvl_name,
                        }
                        maps[lvl_name] = recovered
                    except Exception:
                        pass
    return maps

current_slam_map: Dict[str, Any] = load_initial_slam_map()
slam_maps: Dict[str, Dict[str, Any]] = load_all_slam_maps()

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
            item.pop("diagnostics", None)
            clean_adapters.append(item)

        with open(FLEET_ADAPTERS_FILE, "w", encoding="utf-8") as f:
            json.dump(clean_adapters, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving fleet_adapters.json: {e}")
