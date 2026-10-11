import asyncio
import datetime
import json
from collections import defaultdict, deque
from typing import Dict, Any, List
from fastapi import APIRouter
import state
from state import (
    load_fleet_adapters_data,
    save_fleet_adapters_data,
    broadcast,
)

router = APIRouter(tags=["Fleet Adapters"])

# ==========================================
# In-Memory Real-time Log Buffer (Not persisted to JSON!)
# Keeps latest 100 log lines per adapter in a circular deque
# ==========================================
adapter_logs: Dict[str, deque] = defaultdict(lambda: deque(maxlen=100))

def append_adapter_log(adapter_id: str, message: str) -> None:
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    adapter_logs[adapter_id].append(f"[{timestamp}] {message}")

def get_adapter_logs(adapter_id: str) -> List[str]:
    logs = list(adapter_logs[adapter_id])
    if not logs:
        # Default bootstrap log lines for clean startup display
        return [
            f"[INFO] [easy_full_control]: Fleet adapter '{adapter_id}' online",
            f"[INFO] [easy_full_control]: Connected to Open-RMF Core scheduler",
        ]
    return logs

@router.get("/api/fleet-adapters")
def get_fleet_adapters():
    adapters = load_fleet_adapters_data()
    # Dynamically inject in-memory live logs for UI consumption without polluting disk JSON
    for a in adapters:
        a["logs"] = get_adapter_logs(a.get("id", ""))
        a["diagnostics"] = state.adapter_diagnostics(a)
    return adapters

@router.get("/api/fleet-adapters/{adapter_id}/logs")
def get_fleet_adapter_logs(adapter_id: str):
    return {"adapter_id": adapter_id, "logs": get_adapter_logs(adapter_id)}

@router.post("/api/fleet-adapters/{adapter_id}/logs")
def append_fleet_adapter_log(adapter_id: str, payload: Dict[str, Any]):
    msg = payload.get("message", "Heartbeat pulse OK")
    append_adapter_log(adapter_id, msg)
    return {"status": "success", "logs": get_adapter_logs(adapter_id)}

@router.post("/api/fleet-adapters")
async def save_or_update_fleet_adapter(adapter: Dict[str, Any]):
    adapters = load_fleet_adapters_data()
    adapter_id = adapter.get("id")
    if not adapter_id:
        adapter_id = f"adapter_{len(adapters) + 1}_{int(asyncio.get_event_loop().time())}"
        adapter["id"] = adapter_id

    # Strip logs before saving
    adapter_clean = dict(adapter)
    adapter_clean.pop("logs", None)
    adapter_clean.pop("diagnostics", None)

    idx = next((i for i, a in enumerate(adapters) if a.get("id") == adapter_id), -1)
    if idx >= 0:
        adapters[idx] = adapter_clean
    else:
        adapters.append(adapter_clean)
        append_adapter_log(adapter_id, f"[INFO] [system]: Fleet Adapter '{adapter_clean.get('name')}' created.")

    save_fleet_adapters_data(adapters)

    # Real execution: reload fleet adapter so newly configured kinematics/graphs take effect in Open-RMF
    try:
        from rmf_service import rmf_service
        rmf_service.restart_fleet_adapter()
        append_adapter_log(adapter_id, f"[INFO] [system]: Fleet Adapter reloaded to apply updated parameters.")
    except Exception as e:
        print(f"[WARN] Failed to reload adapter on save: {e}")
    
    # Broadcast with runtime logs attached
    broadcast_adapters = []
    for a in adapters:
        item = dict(a)
        item["logs"] = get_adapter_logs(a.get("id", ""))
        broadcast_adapters.append(item)

    await broadcast({
        "type": "fleet_adapters_updated",
        "adapters": broadcast_adapters
    })
    return {"status": "success", "adapter": adapter_clean}

@router.delete("/api/fleet-adapters/{adapter_id}")
async def delete_fleet_adapter(adapter_id: str):
    adapters = load_fleet_adapters_data()
    adapters = [a for a in adapters if a.get("id") != adapter_id]
    save_fleet_adapters_data(adapters)
    
    # Also clear in-memory logs for deleted adapter
    if adapter_id in adapter_logs:
        del adapter_logs[adapter_id]

    try:
        from rmf_service import rmf_service
        rmf_service.restart_fleet_adapter()
    except Exception as e:
        print(f"[WARN] Failed to reload adapter on delete: {e}")

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
    save_fleet_adapters_data(adapters)

    # Real execution: reload fleet adapter process to apply the new graph configuration
    try:
        from rmf_service import rmf_service
        rmf_service.restart_fleet_adapter()
        log_msg = f"[INFO] [easy_full_control]: Fleet '{adapter.get('fleet_name')}' switched to Graph {new_graph_idx}. Adapter process reloaded successfully."
    except Exception as e:
        log_msg = f"[ERROR] Failed to reload adapter on set_graph: {e}"
    append_adapter_log(adapter_id, log_msg)

    broadcast_adapters = []
    for a in adapters:
        item = dict(a)
        item["logs"] = get_adapter_logs(a.get("id", ""))
        broadcast_adapters.append(item)

    await broadcast({
        "type": "fleet_adapters_updated",
        "adapters": broadcast_adapters
    })
    
    response_adapter = dict(adapter)
    response_adapter["logs"] = get_adapter_logs(adapter_id)
    return {
        "status": "success",
        "message": f"車隊 '{adapter.get('fleet_name')}' 已切換至 Graph {new_graph_idx}，Fleet Adapter 已重新載入以生效新路網！",
        "adapter": response_adapter
    }

@router.post("/api/fleet-adapters/{adapter_id}/action")
async def fleet_adapter_action(adapter_id: str, payload: Dict[str, Any]):
    action = payload.get("action", "restart")
    adapters = load_fleet_adapters_data()
    adapter = next((a for a in adapters if a.get("id") == adapter_id), None)
    if not adapter:
        return {"status": "error", "message": "Adapter not found"}

    fleet_name = adapter.get("fleet_name", "")

    if action == "restart":
        try:
            from rmf_service import rmf_service
            restarted = await asyncio.to_thread(rmf_service.restart_fleet_adapter)
        except Exception as e:
            print(f"[ERROR] Failed to restart fleet adapter process: {e}")
            restarted = False
        if not restarted:
            append_adapter_log(adapter_id, "[ERROR] Fleet Adapter 重新載入失敗，請檢查服務日誌。")
            return {"status": "error", "action": action, "message": "Fleet Adapter 重新載入失敗，路網尚未確認生效。"}
        # Paused fleets survive process restarts.
        adapter["status"] = "standby" if fleet_name in state.paused_fleets else "online"
        append_adapter_log(adapter_id, f"[INFO] [system]: Fleet Adapter '{adapter.get('name')}' service reloaded.")
    elif action in ("pause", "resume"):
        try:
            from rmf_service import rmf_service
            confirmed = await asyncio.to_thread(rmf_service.send_fleet_control, fleet_name, action)
        except Exception as e:
            print(f"[WARN] Failed to dispatch {action} control: {e}")
            confirmed = False
        if not confirmed:
            msg = f"Fleet Adapter 未確認 '{action}'（車隊 {fleet_name}），狀態未變更。"
            append_adapter_log(adapter_id, f"[ERROR] [system]: {msg}")
            return {"status": "error", "action": action, "message": msg}
        if action == "pause":
            adapter["status"] = "standby"
            append_adapter_log(adapter_id, f"[WARN] [system]: Fleet Adapter '{adapter.get('name')}' paused. Decommissioned in Open-RMF.")
        else:
            adapter["status"] = "online"
            append_adapter_log(adapter_id, f"[INFO] [system]: Fleet Adapter '{adapter.get('name')}' resumed online. Recommissioned in Open-RMF.")
    else:
        return {"status": "error", "message": f"Unknown action '{action}'"}

    save_fleet_adapters_data(adapters)

    broadcast_adapters = []
    for a in adapters:
        item = dict(a)
        item["logs"] = get_adapter_logs(a.get("id", ""))
        broadcast_adapters.append(item)

    await broadcast({
        "type": "fleet_adapters_updated",
        "adapters": broadcast_adapters
    })
    
    response_adapter = dict(adapter)
    response_adapter["logs"] = get_adapter_logs(adapter_id)
    return {"status": "success", "action": action, "adapter": response_adapter}
