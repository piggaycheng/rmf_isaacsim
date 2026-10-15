import asyncio
import os
import json
import io
import yaml
from typing import Dict, Any, Optional
from collections import Counter
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from PIL import Image

import nav_graph_builder
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
    # Open-RMF refuses to load a nav graph with duplicated waypoint names, which drops the adapter into mock mode.
    name_counts = Counter(w.get("name", f"wp_{idx}") for idx, w in enumerate(req.waypoints))
    duplicated = sorted(n for n, c in name_counts.items() if c > 1)
    if duplicated:
        raise HTTPException(status_code=400, detail=f"站點名稱重複：{', '.join(duplicated)}，請改名後再儲存")

    levels, lifts = req.levels, req.lifts
    if (levels is None or lifts is None) and os.path.exists(ACTIVE_MAP_FILE):
        try:
            with open(ACTIVE_MAP_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            levels = saved.get("levels") if levels is None else levels
            lifts = saved.get("lifts") if lifts is None else lifts
        except (OSError, ValueError):
            pass
        # A client unaware of levels (e.g. a browser tab with a stale cache) would silently drop the
        # other floors' waypoints, so it must reload the map before saving.
        if req.levels is None and levels and len(levels) > 1:
            raise HTTPException(status_code=409, detail="此地圖為多樓層，請重新整理頁面載入最新路網後再儲存")
    drawing = "current_slam_map.png" if os.path.exists(os.path.join(UPLOAD_DIR, "current_slam_map.png")) else ""
    try:
        built = nav_graph_builder.build(req.name, req.waypoints, req.lanes, req.graphs,
                                        levels, lifts, drawing)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    graphs_list, graph_indices = built["graphs"], built["graph_indices"]

    map_data = {
        "name": req.name,
        "waypoints": req.waypoints,
        "lanes": req.lanes,
        "graphs": graphs_list,
        "levels": built["levels"],
        "lifts": built["lifts"],
    }

    # 1. Save JSON
    with open(ACTIVE_MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(map_data, f, indent=2, ensure_ascii=False)

    # 2. Save Open-RMF Building Map YAML (.building.yaml), one entry per level
    with open(ACTIVE_YAML_FILE, "w", encoding="utf-8") as f:
        yaml.dump(built["building"], f, allow_unicode=True, sort_keys=False)

    # 3. Save Open-RMF Nav Graphs for all graph indices (nav_graphs/{g_idx}.yaml)
    saved_graph_files = []
    for g_idx, g_data in built["nav_graphs"].items():
        g_file = os.path.join(NAV_GRAPHS_DIR, f"{g_idx}.yaml")
        with open(g_file, "w", encoding="utf-8") as f:
            yaml.dump(g_data, f, allow_unicode=True, sort_keys=False)
        saved_graph_files.append(g_file)

    restart_error = None
    if getattr(req, "restart_adapter", False):
        try:
            from rmf_service import rmf_service
            if not await asyncio.to_thread(rmf_service.restart_fleet_adapter):
                restart_error = "Fleet Adapter 重新載入失敗，路網已儲存但尚未確認生效"
        except Exception as e:
            print(f"[Map Save] Note: Fleet adapter reload: {e}")
            restart_error = "Fleet Adapter 重新載入失敗，路網已儲存但尚未確認生效"

    await broadcast({
        "type": "map_updated",
        "map": map_data
    })

    message = f"路網已成功儲存 ({len(req.waypoints)} 個站點，{len(req.lanes)} 條路線，共 {len(graph_indices)} 組路網)"
    stale_fleets = [
        a.get("fleet_name", "")
        for a in state.load_fleet_adapters_data()
        if state.adapter_diagnostics(a).get("status") == "stale"
    ]
    if stale_fleets:
        message += f"；車隊 {', '.join(stale_fleets)} 的 Fleet Adapter 仍使用舊路網，請至車隊管理頁重新載入 Adapter"
    if restart_error:
        message += f"；{restart_error}"

    return {
        "status": "success",
        "message": message,
        "adapter_restart_required": stale_fleets,
        "adapter_restart_error": restart_error,
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
