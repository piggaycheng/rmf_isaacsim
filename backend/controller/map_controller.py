import os
import json
import io
import yaml
from typing import Dict, Any, Optional
from fastapi import APIRouter, UploadFile, File
from fastapi.responses import FileResponse
from PIL import Image

import state
from state import (
    MapSaveRequest,
    UPLOAD_DIR,
    MAPS_DIR,
    NAV_GRAPHS_DIR,
    ACTIVE_MAP_FILE,
    ACTIVE_YAML_FILE,
    ACTIVE_NAV_0_FILE,
    SLAM_METADATA_FILE,
    SLAM_YAML_FILE,
    SLAM_PNG_FILE,
    load_initial_slam_map,
    broadcast,
)

router = APIRouter(tags=["Map & Navigation"])

@router.get("/api/map")
def get_saved_map():
    if os.path.exists(ACTIVE_MAP_FILE):
        try:
            with open(ACTIVE_MAP_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            return {"error": str(e), "waypoints": [], "lanes": [], "graphs": []}
    return {
        "name": "default",
        "waypoints": [],
        "lanes": [],
        "graphs": [{"id": 0, "name": "Graph 0 (預設車隊)", "color": "#38bdf8"}],
    }

@router.get("/api/map/graphs")
def get_available_graphs():
    if os.path.exists(ACTIVE_MAP_FILE):
        try:
            with open(ACTIVE_MAP_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("graphs", [{"id": 0, "name": "Graph 0 (預設車隊)", "color": "#38bdf8"}])
        except Exception:
            pass
    return [{"id": 0, "name": "Graph 0 (預設車隊)", "color": "#38bdf8"}]

@router.post("/api/map")
async def save_map(req: MapSaveRequest):
    default_graphs = [{"id": 0, "name": "Graph 0 (預設車隊)", "color": "#38bdf8"}]
    graphs_list = req.graphs if req.graphs and len(req.graphs) > 0 else default_graphs

    map_data = {
        "name": req.name,
        "waypoints": req.waypoints,
        "lanes": req.lanes,
        "graphs": graphs_list,
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
                            "graph_idx": int(l.get("graph_idx", 0)),
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

    # 3. Save Open-RMF Nav Graphs for all graph indices (nav_graphs/{g_idx}.yaml)
    graph_indices = sorted(list(
        {int(l.get("graph_idx", 0)) for l in req.lanes} |
        {int(g.get("id", 0)) for g in graphs_list} |
        {0}
    ))

    nav_vertices = [
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

    # Ensure Open-RMF requirement: at least one vertex must have is_charger: True
    if nav_vertices and not any(v[2].get("is_charger") for v in nav_vertices):
        nav_vertices[0][2]["is_charger"] = True
        nav_vertices[0][2]["is_parking_spot"] = True

    saved_graph_files = []
    for g_idx in graph_indices:
        g_lanes = []
        g_used_vertex_indices = set()
        for l in req.lanes:
            if int(l.get("graph_idx", 0)) != g_idx:
                continue
            start_idx = next((i for i, w in enumerate(wp_list) if w.get("id") == l.get("start_id")), 0)
            end_idx = next((i for i, w in enumerate(wp_list) if w.get("id") == l.get("end_id")), 0)
            g_used_vertex_indices.add(start_idx)
            g_used_vertex_indices.add(end_idx)
            speed_limit = float(l.get("speed_limit", 0.0))
            is_bidi = bool(l.get("bidirectional", True))
            p = {"speed_limit": speed_limit} if speed_limit > 0.0 else {}
            if is_bidi:
                g_lanes.append([start_idx, end_idx, dict(p)])
                g_lanes.append([end_idx, start_idx, dict(p)])
            else:
                g_lanes.append([start_idx, end_idx, dict(p)])

        # Clone vertices for this graph and ensure at least one used vertex is a charger
        graph_vertices = [
            [v[0], v[1], dict(v[2])]
            for v in nav_vertices
        ]
        has_charger = any(graph_vertices[idx][2].get("is_charger") for idx in g_used_vertex_indices)
        if not has_charger and g_used_vertex_indices:
            charger_idx = min(g_used_vertex_indices)
            graph_vertices[charger_idx][2]["is_charger"] = True
            graph_vertices[charger_idx][2]["is_parking_spot"] = True

        g_data = {
            "building_name": req.name,
            "levels": {
                "L1": {
                    "lanes": g_lanes,
                    "vertices": graph_vertices,
                }
            },
            "doors": {},
            "lifts": {},
        }
        g_file = os.path.join(NAV_GRAPHS_DIR, f"{g_idx}.yaml")
        with open(g_file, "w", encoding="utf-8") as f:
            yaml.dump(g_data, f, allow_unicode=True, sort_keys=False)
        saved_graph_files.append(g_file)

    if getattr(req, "restart_adapter", False):
        try:
            from rmf_service import rmf_service
            rmf_service.restart_fleet_adapter()
        except Exception as e:
            print(f"[Map Save] Note: Fleet adapter reload: {e}")

    await broadcast({
        "type": "map_updated",
        "map": map_data
    })

    return {
        "status": "success",
        "message": f"路網已成功儲存 ({len(req.waypoints)} 個站點，{len(req.lanes)} 條路線，共 {len(graph_indices)} 組路網)",
        "file_json": ACTIVE_MAP_FILE,
        "file_building_yaml": ACTIVE_YAML_FILE,
        "graph_indices": graph_indices,
    }

@router.get("/api/map/download/yaml")
def download_map_yaml():
    if os.path.exists(ACTIVE_YAML_FILE):
        return FileResponse(ACTIVE_YAML_FILE, filename="rmf_building_map.building.yaml", media_type="application/x-yaml")
    return {"error": "尚未儲存路網，無法下載 building.yaml"}

@router.get("/api/map/download/nav_graph_0")
def download_nav_graph_0():
    if os.path.exists(ACTIVE_NAV_0_FILE):
        return FileResponse(ACTIVE_NAV_0_FILE, filename="0.yaml", media_type="application/x-yaml")
    return {"error": "尚未儲存路網，無法下載 0.yaml"}

@router.get("/api/map/download/nav_graph/{graph_idx}")
def download_nav_graph(graph_idx: int):
    target_file = os.path.join(NAV_GRAPHS_DIR, f"{graph_idx}.yaml")
    if os.path.exists(target_file):
        return FileResponse(target_file, filename=f"{graph_idx}.yaml", media_type="application/x-yaml")
    return {"error": f"尚未儲存路網，無法下載 {graph_idx}.yaml"}

@router.post("/api/map/upload-slam")
async def upload_slam_map(
    pgm_file: UploadFile = File(...),
    yaml_file: Optional[UploadFile] = File(None)
):
    try:
        resolution = 0.05
        origin = [-10.0, -10.0, 0.0]
        yaml_bytes = None
        if yaml_file:
            yaml_bytes = await yaml_file.read()
            yaml_data = yaml.safe_load(yaml_bytes.decode('utf-8'))
            if isinstance(yaml_data, dict):
                resolution = float(yaml_data.get("resolution", 0.05))
                origin = list(yaml_data.get("origin", [-10.0, -10.0, 0.0]))

        pgm_bytes = await pgm_file.read()
        image = Image.open(io.BytesIO(pgm_bytes))
        width, height = image.size

        image.save(SLAM_PNG_FILE, "PNG")

        if yaml_bytes:
            with open(SLAM_YAML_FILE, "wb") as yf:
                yf.write(yaml_bytes)

        state.current_slam_map = {
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

        with open(SLAM_METADATA_FILE, "w", encoding="utf-8") as mf:
            json.dump(state.current_slam_map, mf, indent=2, ensure_ascii=False)

        await broadcast({
            "type": "slam_map",
            "map": state.current_slam_map,
        })

        return {"status": "success", "map": state.current_slam_map}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.delete("/api/map/slam")
async def delete_slam_map():
    state.current_slam_map = {}
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

@router.get("/api/map/slam")
def get_slam_map():
    if not state.current_slam_map or not state.current_slam_map.get("image_url"):
        state.current_slam_map = load_initial_slam_map()
    return state.current_slam_map

@router.api_route("/api/map/image", methods=["GET", "HEAD"])
def get_map_image():
    if os.path.exists(SLAM_PNG_FILE):
        return FileResponse(SLAM_PNG_FILE, media_type="image/png")
    return {"error": "No map image available"}
