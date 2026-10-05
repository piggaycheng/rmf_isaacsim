import asyncio
import json
import math
import os
import threading
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from PIL import Image
import yaml
import io
import shutil
import uvicorn

app = FastAPI(title="RMF Web Studio API Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Demo Robots initialization
DEFAULT_ROBOTS = {
    "tinyRobot1": {
        "id": "tinyRobot1",
        "name": "tinyRobot1",
        "fleet": "tinyRobot",
        "x": 2.0,
        "y": 1.0,
        "yaw": 0.0,
        "battery": 94.0,
        "status": "idle",
        "current_task": "等待派發任務",
    },
    "deliveryRobot1": {
        "id": "deliveryRobot1",
        "name": "deliveryRobot1",
        "fleet": "deliveryFleet",
        "x": 8.0,
        "y": 5.0,
        "yaw": 1.57,
        "battery": 78.0,
        "status": "moving",
        "current_task": "前往 coe 巡邏中",
    },
}

current_robots: Dict[str, Dict[str, Any]] = dict(DEFAULT_ROBOTS)
current_tasks: List[Dict[str, Any]] = [
    {
        "id": "task_001",
        "type": "patrol",
        "target_waypoint": "coe",
        "robot_id": "deliveryRobot1",
        "status": "active",
        "progress": 45,
        "created_at": "13:20:10",
    }
]
active_connections: List[WebSocket] = []
last_ros2_time = 0.0

class TaskRequest(BaseModel):
    type: str  # 'patrol', 'delivery', 'goto'
    target_waypoint: str
    destination_waypoint: Optional[str] = None
    robot_id: Optional[str] = None

class MapSaveRequest(BaseModel):
    name: str
    waypoints: List[Dict[str, Any]]
    lanes: List[Dict[str, Any]]

# WebSocket Connection Manager
@app.websocket("/ws/fleet")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    try:
        # Send initial state
        await websocket.send_json({
            "type": "fleet_states",
            "robots": list(current_robots.values()),
        })
        await websocket.send_json({
            "type": "tasks",
            "tasks": current_tasks,
        })
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            action = msg.get("action")
            payload = msg.get("payload", {})

            if action == "dispatch_task":
                task_id = f"task_{len(current_tasks) + 1:03d}"
                target_wp = payload.get("target_waypoint")
                task_type = payload.get("type", "goto")
                assigned_robot = payload.get("robot_id", "tinyRobot1")

                new_task = {
                    "id": task_id,
                    "type": task_type,
                    "target_waypoint": target_wp,
                    "destination_waypoint": payload.get("destination_waypoint"),
                    "robot_id": assigned_robot,
                    "status": "active",
                    "progress": 0,
                }
                current_tasks.insert(0, new_task)

                # Update robot state
                if assigned_robot in current_robots:
                    current_robots[assigned_robot]["status"] = "moving"
                    current_robots[assigned_robot]["current_task"] = f"執行 {task_type} 前往 {target_wp}"

                await broadcast({
                    "type": "tasks",
                    "tasks": current_tasks,
                })
                await broadcast({
                    "type": "fleet_states",
                    "robots": list(current_robots.values()),
                })
    except WebSocketDisconnect:
        if websocket in active_connections:
            active_connections.remove(websocket)

async def broadcast(message: dict):
    for connection in list(active_connections):
        try:
            await connection.send_json(message)
        except Exception:
            pass

# Background Simulation Loop (Simulates smooth movement if no live ROS 2 /fleet_states yet)
async def simulation_loop():
    while True:
        await asyncio.sleep(0.1)
        now = asyncio.get_event_loop().time()
        # Only simulate if ROS 2 hasn't published within last 2 seconds
        if now - last_ros2_time > 2.0:
            for robot in current_robots.values():
                if robot["status"] == "moving":
                    target_x = 8.0 if robot["id"] == "tinyRobot1" else 2.0
                    target_y = 5.0 if robot["id"] == "tinyRobot1" else 1.0
                    dx = target_x - robot["x"]
                    dy = target_y - robot["y"]
                    dist = math.hypot(dx, dy)
                    if dist < 0.1:
                        robot["status"] = "idle"
                        robot["current_task"] = "已抵達目標點"
                    else:
                        step = 0.06
                        robot["x"] += (dx / dist) * step
                        robot["y"] += (dy / dist) * step
                        robot["yaw"] = math.atan2(dy, dx)
                        robot["battery"] = max(10.0, robot["battery"] - 0.02)

            for t in current_tasks:
                if t["status"] == "active":
                    t["progress"] = min(100, t["progress"] + 1)
                    if t["progress"] >= 100:
                        t["status"] = "completed"

            if active_connections:
                await broadcast({
                    "type": "fleet_states",
                    "robots": list(current_robots.values()),
                })
                await broadcast({
                    "type": "tasks",
                    "tasks": current_tasks,
                })

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(simulation_loop())
    # Start ROS 2 listener in background thread if available
    try:
        import rclpy
        from rclpy.node import Node
        from rmf_fleet_msgs.msg import FleetState

        def ros2_spin():
            rclpy.init()
            node = Node("rmf_web_studio_bridge")

            def fleet_callback(msg: FleetState):
                nonlocal node
                fleet_name = msg.name
                for r in msg.robots:
                    r_id = r.name
                    current_robots[r_id] = {
                        "id": r_id,
                        "name": r_id,
                        "fleet": fleet_name,
                        "x": r.location.x,
                        "y": r.location.y,
                        "yaw": r.location.yaw,
                        "battery": r.battery_percent,
                        "status": "moving" if r.mode.mode == 2 else "charging" if r.mode.mode == 3 else "idle",
                        "current_task": r.task_id or "工作中",
                    }

            node.create_subscription(FleetState, "/fleet_states", fleet_callback, 10)
            rclpy.spin(node)

        t = threading.Thread(target=ros2_spin, daemon=True)
        t.start()
    except Exception as e:
        print(f"ROS 2 background bridge startup notice: {e}")

# REST Endpoints
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
            # Auto-save metadata so next time it is ready
            with open(SLAM_METADATA_FILE, "w", encoding="utf-8") as f:
                json.dump(recovered, f, indent=2, ensure_ascii=False)
            return recovered
        except Exception as e:
            print(f"Notice: Failed to recover SLAM map from PNG: {e}")
    return {}

current_slam_map: Dict[str, Any] = load_initial_slam_map()

class MapSaveRequest(BaseModel):
    name: str = "office_map"
    waypoints: List[Dict[str, Any]]
    lanes: List[Dict[str, Any]]

@app.get("/api/health")
def health_check():
    return {"status": "ok", "active_clients": len(active_connections)}

@app.get("/api/robots")
def get_robots():
    return list(current_robots.values())

@app.get("/api/tasks")
def get_tasks():
    return current_tasks

@app.get("/api/map")
def get_saved_map():
    if os.path.exists(ACTIVE_MAP_FILE):
        try:
            with open(ACTIVE_MAP_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            return {"error": str(e), "waypoints": [], "lanes": []}
    return {"name": "default", "waypoints": [], "lanes": []}

@app.post("/api/map")
async def save_map(req: MapSaveRequest):
    map_data = {
        "name": req.name,
        "waypoints": req.waypoints,
        "lanes": req.lanes,
    }
    
    # 1. Save JSON
    with open(ACTIVE_MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(map_data, f, indent=2, ensure_ascii=False)
        
    # 2. Save Open-RMF Building Map YAML (.building.yaml)
    wp_list = req.waypoints
    rmf_building_structure = {
        "name": req.name,
        "levels": {
            "L1": {
                "drawing": {
                    "filename": "current_slam_map.png" if os.path.exists(os.path.join(UPLOAD_DIR, "current_slam_map.png")) else ""
                },
                "vertices": [
                    [
                        float(w.get("x", 0.0)),
                        float(w.get("y", 0.0)),
                        0.0,
                        w.get("name", f"wp_{idx}"),
                        {
                            "is_charger": w.get("type") == "charger",
                            "is_parking_spot": w.get("type") == "parking",
                            "is_holding_point": w.get("type") == "parking",
                            "is_workcell": w.get("type") == "workcell"
                        }
                    ]
                    for idx, w in enumerate(wp_list)
                ],
                "lanes": [
                    [
                        next((i for i, w in enumerate(wp_list) if w.get("id") == l.get("start_id")), 0),
                        next((i for i, w in enumerate(wp_list) if w.get("id") == l.get("end_id")), 0),
                        {
                            "is_bidirectional": bool(l.get("bidirectional", True)),
                            "graph_idx": 0,
                            "speed_limit": float(l.get("speed_limit", 0.0))
                        }
                    ]
                    for l in req.lanes
                ]
            }
        }
    }
    with open(ACTIVE_YAML_FILE, "w", encoding="utf-8") as f:
        yaml.dump(rmf_building_structure, f, allow_unicode=True, sort_keys=False)

    # 3. Save Open-RMF Nav Graph (nav_graphs/0.yaml for fleet adapter)
    nav_0_lanes = []
    for l in req.lanes:
        start_idx = next((i for i, w in enumerate(wp_list) if w.get("id") == l.get("start_id")), 0)
        end_idx = next((i for i, w in enumerate(wp_list) if w.get("id") == l.get("end_id")), 0)
        speed_limit = float(l.get("speed_limit", 0.0))
        is_bidi = bool(l.get("bidirectional", True))
        p = {"speed_limit": speed_limit} if speed_limit > 0.0 else {}
        if is_bidi:
            nav_0_lanes.append([start_idx, end_idx, dict(p)])
            nav_0_lanes.append([end_idx, start_idx, dict(p)])
        else:
            nav_0_lanes.append([start_idx, end_idx, dict(p)])

    nav_0_vertices = [
        [
            float(w.get("x", 0.0)),
            float(w.get("y", 0.0)),
            {
                "is_charger": w.get("type") == "charger",
                "is_parking_spot": w.get("type") == "parking",
                "is_holding_point": w.get("type") == "parking",
                "is_workcell": w.get("type") == "workcell",
                "name": w.get("name", f"wp_{idx}"),
            },
        ]
        for idx, w in enumerate(wp_list)
    ]

    nav_0_data = {
        "building_name": req.name,
        "levels": {
            "L1": {
                "lanes": nav_0_lanes,
                "vertices": nav_0_vertices,
            }
        },
        "doors": {},
        "lifts": {},
    }
    with open(ACTIVE_NAV_0_FILE, "w", encoding="utf-8") as f:
        yaml.dump(nav_0_data, f, allow_unicode=True, sort_keys=False)

    await broadcast({
        "type": "map_updated",
        "map": map_data
    })

    return {
        "status": "success",
        "message": f"路網已成功儲存 ({len(req.waypoints)} 個站點，{len(req.lanes)} 條路線)",
        "file_json": ACTIVE_MAP_FILE,
        "file_building_yaml": ACTIVE_YAML_FILE,
        "file_nav_0_yaml": ACTIVE_NAV_0_FILE,
    }

@app.get("/api/map/download/yaml")
def download_map_yaml():
    if os.path.exists(ACTIVE_YAML_FILE):
        return FileResponse(ACTIVE_YAML_FILE, filename="rmf_building_map.building.yaml", media_type="application/x-yaml")
    return {"error": "尚未儲存路網，無法下載 building.yaml"}

@app.get("/api/map/download/nav_graph_0")
def download_nav_graph_0():
    if os.path.exists(ACTIVE_NAV_0_FILE):
        return FileResponse(ACTIVE_NAV_0_FILE, filename="0.yaml", media_type="application/x-yaml")
    return {"error": "尚未儲存路網，無法下載 0.yaml"}

@app.post("/api/map/upload-slam")
async def upload_slam_map(
    pgm_file: UploadFile = File(...),
    yaml_file: Optional[UploadFile] = File(None)
):
    global current_slam_map
    try:
        # 1. Read and parse YAML file if provided
        resolution = 0.05
        origin = [-10.0, -10.0, 0.0]
        yaml_bytes = None
        if yaml_file:
            yaml_bytes = await yaml_file.read()
            yaml_data = yaml.safe_load(yaml_bytes.decode('utf-8'))
            if isinstance(yaml_data, dict):
                resolution = float(yaml_data.get("resolution", 0.05))
                origin = list(yaml_data.get("origin", [-10.0, -10.0, 0.0]))

        # 2. Read and convert PGM image to PNG
        pgm_bytes = await pgm_file.read()
        image = Image.open(io.BytesIO(pgm_bytes))
        width, height = image.size

        image.save(SLAM_PNG_FILE, "PNG")

        # 3. Save raw YAML file to disk if uploaded
        if yaml_bytes:
            with open(SLAM_YAML_FILE, "wb") as yf:
                yf.write(yaml_bytes)

        current_slam_map = {
            "name": pgm_file.filename or "slam_map",
            "image_url": "/api/map/image",
            "resolution": resolution,
            "origin": origin,
            "width": width,
            "height": height,
            "real_width_m": width * resolution,
            "real_height_m": height * resolution,
            "opacity": 0.85,
        }

        # 4. Save metadata JSON to disk (permanent persistence across restarts!)
        with open(SLAM_METADATA_FILE, "w", encoding="utf-8") as mf:
            json.dump(current_slam_map, mf, indent=2, ensure_ascii=False)

        # Broadcast update to web clients
        await broadcast({
            "type": "slam_map",
            "map": current_slam_map,
        })

        return {"status": "success", "map": current_slam_map}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.delete("/api/map/slam")
async def delete_slam_map():
    global current_slam_map
    current_slam_map = {}
    for path in [SLAM_METADATA_FILE, SLAM_YAML_FILE, SLAM_PNG_FILE]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass
    await broadcast({
        "type": "slam_map",
        "map": None,
    })
    return {"status": "success", "message": "SLAM 地圖已完全清除"}

@app.get("/api/map/slam")
def get_slam_map():
    global current_slam_map
    if not current_slam_map or not current_slam_map.get("image_url"):
        current_slam_map = load_initial_slam_map()
    return current_slam_map

@app.api_route("/api/map/image", methods=["GET", "HEAD"])
def get_map_image():
    if os.path.exists(SLAM_PNG_FILE):
        return FileResponse(SLAM_PNG_FILE, media_type="image/png")
    return {"error": "No map image available"}

# MediaMTX stream readiness checking endpoint
@app.get("/api/stream/status/{stream_name:path}")
def get_stream_status(stream_name: str):
    import urllib.request
    try:
        url = f"http://127.0.0.1:9997/v3/paths/get/{stream_name}"
        with urllib.request.urlopen(url, timeout=0.5) as r:
            data = json.loads(r.read())
            return {"ready": bool(data.get("ready")), "online": bool(data.get("online"))}
    except Exception:
        return {"ready": False, "online": False}

# Serve compiled frontend static files
FRONTEND_DIST = os.path.abspath(os.path.join(os.path.dirname(__file__), "../frontend/dist"))
if os.path.exists(FRONTEND_DIST):
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="static")

if __name__ == "__main__":
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_excludes=["saved_maps/*", "uploads/*", "*.png", "*.yaml", "*.json", "*.pgm"],
    )
