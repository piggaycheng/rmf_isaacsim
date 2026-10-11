import os
import json
import asyncio
import threading
import urllib.request
from typing import Dict
from fastapi import APIRouter
from state import CameraDisableRequest

router = APIRouter(tags=["Cameras & Streams"])

# ==========================================
# MediaMTX Watchdog Camera Controller
# (Routes via Fleet Adapter ROS 2 -> MQTT)
# ==========================================

def disable_camera_stream(camera_name: str, topic_prefix: str = "slam/cameras") -> bool:
    """Instructs Fleet Adapter via ROS 2 to disable the camera stream over MQTT."""
    try:
        from rmf_service import rmf_service
        ok = rmf_service.send_camera_control(camera_name, topic_prefix, enabled=False)
        if ok:
            print(f"[Camera Watchdog] Dispatched disable control for '{camera_name}' via Fleet Adapter.")
            return True
        else:
            print(f"[Camera Watchdog WARN] RMF service bridge not ready for camera '{camera_name}'.")
            return False
    except Exception as e:
        print(f"[Camera Watchdog ERROR] Failed to dispatch camera disable: {e}")
        return False

# Trackers for MediaMTX zero-reader idle monitoring
zero_readers_tracker: Dict[str, float] = {}
path_first_ready_tracker: Dict[str, float] = {}

async def mediamtx_watchdog_loop():
    """
    Background Watchdog:
    Periodically checks MediaMTX active streams via REST API.
    If a stream is ready but has 0 readers (viewers) continuously for 4 seconds,
    automatically shuts down the camera via MQTT to conserve GPU and simulator resources.
    """
    print("[MediaMTX Watchdog] Started camera stream idle watchdog service.")
    while True:
        try:
            await asyncio.sleep(2.0)
            now = asyncio.get_event_loop().time()
            url = "http://127.0.0.1:9997/v3/paths/list"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                data = json.loads(resp.read().decode())

            items = data.get("items", [])
            current_active_names = {item.get("name") for item in items if item.get("name")}

            # Prune removed paths from trackers
            for old_name in list(zero_readers_tracker.keys()):
                if old_name not in current_active_names:
                    del zero_readers_tracker[old_name]
            for old_name in list(path_first_ready_tracker.keys()):
                if old_name not in current_active_names:
                    del path_first_ready_tracker[old_name]

            for item in items:
                name = item.get("name")
                ready = item.get("ready", False)
                readers = item.get("readers", [])

                if not ready or not name:
                    continue

                if name not in path_first_ready_tracker:
                    path_first_ready_tracker[name] = now

                # Initial startup grace period (10 seconds) for WebRTC handshake
                if now - path_first_ready_tracker[name] < 10.0:
                    continue

                if len(readers) > 0:
                    # Active viewers present: reset idle timer
                    if name in zero_readers_tracker:
                        del zero_readers_tracker[name]
                else:
                    # Zero viewers detected!
                    if name not in zero_readers_tracker:
                        zero_readers_tracker[name] = now
                    else:
                        idle_duration = now - zero_readers_tracker[name]
                        if idle_duration >= 4.0:
                            print(f"[MediaMTX Watchdog] Stream '{name}' has had 0 viewers for {idle_duration:.1f}s. Automatically turning off camera...")
                            disable_camera_stream(name)
                            del zero_readers_tracker[name]
                            if name in path_first_ready_tracker:
                                del path_first_ready_tracker[name]
        except Exception:
            pass

@router.get("/api/stream/status/{stream_name:path}")
def get_stream_status(stream_name: str):
    try:
        url = f"http://127.0.0.1:9997/v3/paths/get/{stream_name}"
        with urllib.request.urlopen(url, timeout=0.5) as r:
            data = json.loads(r.read())
            return {"ready": bool(data.get("ready")), "online": bool(data.get("online"))}
    except Exception:
        return {"ready": False, "online": False}

@router.post("/api/stream/camera/disable")
def api_disable_camera(req: CameraDisableRequest):
    ok = disable_camera_stream(req.camera_name, req.topic_prefix or "slam/cameras")
    return {"status": "ok" if ok else "error", "camera": req.camera_name}
