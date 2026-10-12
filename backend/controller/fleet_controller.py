import asyncio
import json
import math
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel
import state
from state import (
    current_robots,
    current_tasks,
    active_connections,
    broadcast,
    get_discovered_robots_list,
    TaskRequest,
)
from rmf_service import rmf_service

router = APIRouter(tags=["Fleet & Tasks"])

@router.get("/api/health")
def health_check():
    return {"status": "ok", "active_clients": len(active_connections)}

@router.get("/api/robots")
def get_robots():
    return list(current_robots.values())

@router.get("/api/tasks")
def get_tasks():
    return current_tasks

@router.post("/api/tasks/dispatch")
async def dispatch_task_endpoint(req: TaskRequest):
    """Submits task dispatch request to Open-RMF scheduler."""
    assigned_robot = req.robot_id if req.robot_id != "any" else None
    res = rmf_service.dispatch_task(
        task_type=req.type,
        target_waypoint=req.target_waypoint,
        destination_waypoint=req.destination_waypoint,
        robot_id=assigned_robot,
    )
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message", "任務派遣失敗"))
    await broadcast({
        "type": "tasks",
        "tasks": current_tasks,
    })
    return res

class EmergencyStopRequest(BaseModel):
    fleet_name: Optional[str] = None
    cancel_task: bool = False


async def _broadcast_fleet_and_tasks():
    await broadcast({"type": "fleet_states", "robots": list(current_robots.values())})
    await broadcast({"type": "tasks", "tasks": current_tasks})


@router.post("/api/tasks/{task_id}/cancel")
async def cancel_task_endpoint(task_id: str):
    """Cancels a queued or in-progress task (local id, request id, or Open-RMF booking id)."""
    res = await asyncio.to_thread(rmf_service.cancel_task, task_id)
    await _broadcast_fleet_and_tasks()
    return res


async def _robot_control(robot_id: str, action: str, fleet_name: Optional[str], cancel_task: bool = False) -> Dict[str, Any]:
    fleet = fleet_name or state.find_robot_fleet(robot_id)
    if not fleet:
        return {"status": "error", "robot_id": robot_id, "message": f"找不到車輛 '{robot_id}' 所屬車隊。"}
    task_id = (current_robots.get(robot_id) or {}).get("task_id") or ""
    confirmed = await asyncio.to_thread(rmf_service.send_robot_control, fleet, robot_id, action)
    if not confirmed:
        label = "緊急停車" if action == "estop" else "解除緊急停車"
        return {"status": "error", "robot_id": robot_id, "fleet_name": fleet,
                "message": f"Fleet Adapter 未確認{label}（{fleet}/{robot_id}）。"}
    result: Dict[str, Any] = {"status": "success", "robot_id": robot_id, "fleet_name": fleet,
                              "emergency_stop": action == "estop"}
    if action == "estop":
        result["message"] = f"車輛 '{robot_id}' 已緊急停車；解除前不會再執行任何指令。"
        if cancel_task and task_id:
            result["cancel_result"] = await asyncio.to_thread(rmf_service.cancel_task, task_id)
    else:
        result["message"] = f"車輛 '{robot_id}' 已解除緊急停車；若原任務仍有效將繼續執行。"
    return result


@router.post("/api/robots/{robot_id}/emergency-stop")
async def emergency_stop_robot(robot_id: str, req: Optional[EmergencyStopRequest] = None):
    """Stops the robot immediately and holds all RMF commands until released. Optionally cancels its task."""
    req = req or EmergencyStopRequest()
    res = await _robot_control(robot_id, "estop", req.fleet_name, req.cancel_task)
    await _broadcast_fleet_and_tasks()
    return res


@router.post("/api/robots/{robot_id}/emergency-stop/release")
async def release_emergency_stop_robot(robot_id: str, req: Optional[EmergencyStopRequest] = None):
    """Releases the emergency stop; the interrupted command is resumed if its task is still active."""
    req = req or EmergencyStopRequest()
    res = await _robot_control(robot_id, "release", req.fleet_name)
    await _broadcast_fleet_and_tasks()
    return res


@router.post("/api/emergency-stop")
async def emergency_stop_all(req: Optional[EmergencyStopRequest] = None):
    """Emergency-stops every known robot."""
    req = req or EmergencyStopRequest()
    robots = list(current_robots.values())
    results = await asyncio.gather(*[
        _robot_control(r["id"], "estop", r.get("fleet"), req.cancel_task) for r in robots
    ])
    await _broadcast_fleet_and_tasks()
    ok = all(r.get("status") == "success" for r in results)
    return {"status": "success" if ok else "error", "results": results}


@router.post("/api/emergency-stop/release")
async def release_emergency_stop_all():
    """Releases the emergency stop of every e-stopped robot."""
    targets = [key.split("/", 1) for key in sorted(state.estopped_robots) if "/" in key]
    results = await asyncio.gather(*[_robot_control(rid, "release", fleet) for fleet, rid in targets])
    await _broadcast_fleet_and_tasks()
    ok = all(r.get("status") == "success" for r in results)
    return {"status": "success" if ok else "error", "results": results}
@router.websocket("/ws/fleet")
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
        await websocket.send_json({
            "type": "discovered_robots",
            "discovered": get_discovered_robots_list(),
        })
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            action = msg.get("action")
            payload = msg.get("payload", {})

            if action == "dispatch_task":
                target_wp = payload.get("target_waypoint")
                task_type = payload.get("type", "goto")
                assigned_robot = payload.get("robot_id")
                if assigned_robot == "any":
                    assigned_robot = None
                dest_wp = payload.get("destination_waypoint")

                # Dispatch via Open-RMF scheduler
                res = rmf_service.dispatch_task(
                    task_type=task_type,
                    target_waypoint=target_wp,
                    destination_waypoint=dest_wp,
                    robot_id=assigned_robot,
                )

                # Send explicit dispatch response back to the requesting client
                await websocket.send_json({
                    "type": "dispatch_response",
                    "status": res.get("status"),
                    "task_id": res.get("task_id"),
                    "request_id": res.get("request_id"),
                    "message": res.get("message"),
                })

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

# Background Broadcast & Optional Simulation Loop
async def simulation_loop():
    while True:
        await asyncio.sleep(0.1)
        # Simulation only runs if explicitly enabled (isolated from real mode)
        if getattr(state, "simulation_mode", False):
            for robot in current_robots.values():
                if robot.get("status") == "moving":
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

        # Continuous real-time broadcast to connected Web Studio clients
        if active_connections:
            await broadcast({
                "type": "fleet_states",
                "robots": list(current_robots.values()),
            })
            await broadcast({
                "type": "tasks",
                "tasks": current_tasks,
            })

