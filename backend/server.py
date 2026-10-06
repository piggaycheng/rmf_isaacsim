import os
import sys
import asyncio
import threading
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Ensure backend directory is in Python module search path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from state import current_robots
from controller.fleet_controller import router as fleet_router, simulation_loop
from controller.map_controller import router as map_router
from controller.adapter_controller import router as adapter_router
from controller.camera_controller import router as camera_router, mediamtx_watchdog_loop

app = FastAPI(title="RMF Web Studio API Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Modular Routers from Controller Package
app.include_router(fleet_router)
app.include_router(map_router)
app.include_router(adapter_router)
app.include_router(camera_router)

@app.on_event("startup")
async def startup_event():
    # 1. Background simulation and MediaMTX idle stream watchdog
    asyncio.create_task(simulation_loop())
    asyncio.create_task(mediamtx_watchdog_loop())

    # 2. Start ROS 2 listener in background thread if available
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
