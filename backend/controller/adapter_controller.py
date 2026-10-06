import asyncio
from typing import Dict, Any
from fastapi import APIRouter
from state import (
    load_fleet_adapters_data,
    save_fleet_adapters_data,
    broadcast,
)

router = APIRouter(tags=["Fleet Adapters"])

@router.get("/api/fleet-adapters")
def get_fleet_adapters():
    return load_fleet_adapters_data()

@router.post("/api/fleet-adapters")
async def save_or_update_fleet_adapter(adapter: Dict[str, Any]):
    adapters = load_fleet_adapters_data()
    adapter_id = adapter.get("id")
    if not adapter_id:
        adapter_id = f"adapter_{len(adapters) + 1}_{int(asyncio.get_event_loop().time())}"
        adapter["id"] = adapter_id

    idx = next((i for i, a in enumerate(adapters) if a.get("id") == adapter_id), -1)
    if idx >= 0:
        adapters[idx] = adapter
    else:
        adapters.append(adapter)
    save_fleet_adapters_data(adapters)
    await broadcast({
        "type": "fleet_adapters_updated",
        "adapters": adapters
    })
    return {"status": "success", "adapter": adapter}

@router.delete("/api/fleet-adapters/{adapter_id}")
async def delete_fleet_adapter(adapter_id: str):
    adapters = load_fleet_adapters_data()
    adapters = [a for a in adapters if a.get("id") != adapter_id]
    save_fleet_adapters_data(adapters)
    await broadcast({
        "type": "fleet_adapters_updated",
        "adapters": adapters
    })
    return {"status": "success", "message": f"Adapter {adapter_id} 已刪除"}

@router.post("/api/fleet-adapters/{adapter_id}/set-graph")
async def set_fleet_adapter_graph(adapter_id: str, payload: Dict[str, Any]):
    new_graph_idx = int(payload.get("graph_idx", 0))
    adapters = load_fleet_adapters_data()
    adapter = next((a for a in adapters if a.get("id") == adapter_id), None)
    if not adapter:
        return {"status": "error", "message": "Adapter not found"}

    old_idx = adapter.get("graph_idx", 0)
    adapter["graph_idx"] = new_graph_idx
    log_msg = f"[INFO] [easy_full_control]: Executed dynamic set_graph({new_graph_idx}) from Graph {old_idx} -> Success (0 restart needed)"
    if "logs" not in adapter or not isinstance(adapter["logs"], list):
        adapter["logs"] = []
    adapter["logs"].append(log_msg)
    save_fleet_adapters_data(adapters)

    await broadcast({
        "type": "fleet_adapters_updated",
        "adapters": adapters
    })
    return {
        "status": "success",
        "message": f"車隊 '{adapter.get('fleet_name')}' 已成功動態切換至 Graph {new_graph_idx}！",
        "adapter": adapter
    }

@router.post("/api/fleet-adapters/{adapter_id}/action")
async def fleet_adapter_action(adapter_id: str, payload: Dict[str, Any]):
    action = payload.get("action", "restart")
    adapters = load_fleet_adapters_data()
    adapter = next((a for a in adapters if a.get("id") == adapter_id), None)
    if not adapter:
        return {"status": "error", "message": "Adapter not found"}

    if "logs" not in adapter or not isinstance(adapter["logs"], list):
        adapter["logs"] = []

    if action == "restart":
        adapter["status"] = "online"
        adapter["logs"].append(f"[INFO] [system]: Fleet Adapter '{adapter.get('name')}' service reloaded.")
    elif action == "pause":
        adapter["status"] = "standby"
        adapter["logs"].append(f"[WARN] [system]: Fleet Adapter '{adapter.get('name')}' paused.")
    elif action == "resume":
        adapter["status"] = "online"
        adapter["logs"].append(f"[INFO] [system]: Fleet Adapter '{adapter.get('name')}' resumed online.")

    save_fleet_adapters_data(adapters)
    await broadcast({
        "type": "fleet_adapters_updated",
        "adapters": adapters
    })
    return {"status": "success", "action": action, "adapter": adapter}
