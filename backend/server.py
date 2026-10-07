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
from controller.discovery_controller import router as discovery_router, discovery_watchdog_loop

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
app.include_router(discovery_router)

from rmf_service import rmf_service

@app.on_event("startup")
async def startup_event():
    # 1. Background simulation, MediaMTX watchdog, and MQTT robot auto-discovery
    asyncio.create_task(simulation_loop())
    asyncio.create_task(mediamtx_watchdog_loop())
    asyncio.create_task(discovery_watchdog_loop())

    # 2. Start Open-RMF core services (Schedule, Dispatcher, MQTT Fleet Adapter) and ROS 2 Bridge
    try:
        rmf_service.start_background_processes()
        rmf_service.init_ros2_node()
    except Exception as e:
        print(f"RMF Core startup notice: {e}")

@app.on_event("shutdown")
def shutdown_event():
    rmf_service.shutdown()

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
