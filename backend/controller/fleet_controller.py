import asyncio
import json
import math
from typing import Dict, Any, List
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from state import (
    current_robots,
    current_tasks,
    active_connections,
    last_ros2_time,
    broadcast,
)

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
