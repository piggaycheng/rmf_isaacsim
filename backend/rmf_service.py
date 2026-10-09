import os
import sys
import json
import time
import asyncio
import subprocess
import threading
from typing import Dict, Any, List, Optional

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, QoSDurabilityPolicy, QoSReliabilityPolicy
from rmf_fleet_msgs.msg import FleetState
from rmf_task_msgs.msg import ApiRequest, ApiResponse, DispatchStates

import state
from controller.adapter_controller import append_adapter_log

class RMFService:
    """
    Manages Open-RMF core ROS 2 background nodes, MQTT Fleet Adapter process,
    and ROS 2 Task Dispatch API communication.
    """
    def __init__(self):
        self.schedule_process: Optional[subprocess.Popen] = None
        self.dispatcher_process: Optional[subprocess.Popen] = None
        self.adapter_process: Optional[subprocess.Popen] = None
        self.ros_node: Optional[Node] = None
        self.task_pub = None
        self.running = False
        self.pending_requests: Dict[str, asyncio.Future] = {}
        self.lock = threading.Lock()

    def start_background_processes(self):
        """Starts rmf_traffic_schedule, rmf_task_dispatcher and mqtt_fleet_adapter."""
        env = os.environ.copy()

        # Clean up any stale or defunct background processes first
        try:
            subprocess.run(["pkill", "-9", "-f", "rmf_traffic_schedule"], capture_output=True)
            subprocess.run(["pkill", "-9", "-f", "rmf_task_dispatcher"], capture_output=True)
            subprocess.run(["pkill", "-9", "-f", "mqtt_fleet_adapter.py"], capture_output=True)
            time.sleep(0.5)
        except Exception:
            pass

        # 1. Start rmf_traffic_schedule
        try:
            print("[RMF Core] Starting rmf_traffic_schedule...")
            self.schedule_process = subprocess.Popen(
                ["bash", "-c", "source /ros_entrypoint.sh && ros2 run rmf_traffic_ros2 rmf_traffic_schedule"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                env=env,
            )
            time.sleep(1.5)
            print("[RMF Core] ✅ rmf_traffic_schedule started.")
        except Exception as e:
            print(f"[RMF Core ERROR] Failed to start rmf_traffic_schedule: {e}")

        # 2. Start rmf_task_dispatcher
        try:
            print("[RMF Core] Starting rmf_task_dispatcher...")
            self.dispatcher_process = subprocess.Popen(
                ["bash", "-c", "source /ros_entrypoint.sh && ros2 run rmf_task_ros2 rmf_task_dispatcher"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                env=env,
            )
            time.sleep(1.5)
            print("[RMF Core] ✅ rmf_task_dispatcher started.")
        except Exception as e:
            print(f"[RMF Core ERROR] Failed to start rmf_task_dispatcher: {e}")

        # 3. Start mqtt_fleet_adapter if not already running
        try:
            check_fa = subprocess.run(["pgrep", "-f", "mqtt_fleet_adapter.py"], capture_output=True)
            if check_fa.returncode != 0:
                adapter_script = os.path.abspath(
                    os.path.join(os.path.dirname(__file__), "../workspace/mqtt_fleet_adapter/mqtt_fleet_adapter.py")
                )
                if not os.path.exists(adapter_script):
                    adapter_script = "/root/rmf_ws/src/custom_ws/mqtt_fleet_adapter/mqtt_fleet_adapter.py"

                if os.path.exists(adapter_script):
                    print(f"[RMF FA] Starting Open-RMF MQTT Fleet Adapter ({adapter_script})...")
                    log_file = open("/tmp/adapter.log", "w")
                    self.adapter_process = subprocess.Popen(
                        ["bash", "-c", f"source /ros_entrypoint.sh && python3 -u {adapter_script}"],
                        stdout=log_file,
                        stderr=log_file,
                        env=env,
                    )
                    time.sleep(1.5)
                    print("[RMF FA] ✅ Open-RMF MQTT Fleet Adapter started.")
                    append_adapter_log("system", "Open-RMF MQTT Fleet Adapter background service started.")
            else:
                print("[RMF FA] mqtt_fleet_adapter.py is already active.")
        except Exception as e:
            print(f"[RMF FA ERROR] Failed to start mqtt_fleet_adapter: {e}")

    def restart_fleet_adapter(self) -> bool:
        """Kills and restarts mqtt_fleet_adapter to reload updated nav graphs from disk."""
        env = os.environ.copy()
        try:
            print("[RMF FA] Restarting Open-RMF MQTT Fleet Adapter to reload nav graphs...")
            subprocess.run(["pkill", "-9", "-f", "mqtt_fleet_adapter.py"], capture_output=True)
            time.sleep(0.8)

            adapter_script = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "../workspace/mqtt_fleet_adapter/mqtt_fleet_adapter.py")
            )
            if not os.path.exists(adapter_script):
                adapter_script = "/root/rmf_ws/src/custom_ws/mqtt_fleet_adapter/mqtt_fleet_adapter.py"

            if os.path.exists(adapter_script):
                log_file = open("/tmp/adapter.log", "w")
                self.adapter_process = subprocess.Popen(
                    ["bash", "-c", f"source /ros_entrypoint.sh && python3 -u {adapter_script}"],
                    stdout=log_file,
                    stderr=log_file,
                    env=env,
                )
                time.sleep(1.0)
                print("[RMF FA] ✅ Open-RMF MQTT Fleet Adapter restarted successfully.")
                append_adapter_log("system", "Fleet Adapter 重啟完成，已重新載入最新路網。")
                return True
        except Exception as e:
            print(f"[RMF FA ERROR] Failed to restart mqtt_fleet_adapter: {e}")
            append_adapter_log("system", f"Fleet Adapter 重啟失敗: {e}")
            return False
        return False

    def init_ros2_node(self):
        """Initializes ROS 2 node for /fleet_states and /task_api_requests."""
        try:
            if not rclpy.ok():
                rclpy.init()

            self.ros_node = Node("rmf_web_studio_bridge")

            # QoS for Task API: TRANSIENT_LOCAL + RELIABLE
            task_qos = QoSProfile(depth=10)
            task_qos.durability = QoSDurabilityPolicy.TRANSIENT_LOCAL
            task_qos.reliability = QoSReliabilityPolicy.RELIABLE

            self.task_pub = self.ros_node.create_publisher(
                ApiRequest, "/task_api_requests", task_qos
            )

            # Subscribe to Task API responses
            def task_response_cb(msg: ApiResponse):
                try:
                    payload = json.loads(msg.json_msg)
                    req_id = msg.request_id
                    print(f"[RMF Task API Response] ID: {req_id}, payload: {payload}")
                    
                    # Update active tasks in state
                    booking_id = payload.get("state", {}).get("booking", {}).get("id") or req_id
                    status = payload.get("state", {}).get("status", "active")
                    
                    for t in state.current_tasks:
                        if t.get("id") == req_id or t.get("booking_id") == req_id:
                            t["status"] = status
                            t["booking_id"] = booking_id
                            break
                            
                except Exception as ex:
                    print(f"[RMF Task API] Error parsing response: {ex}")

            self.ros_node.create_subscription(
                ApiResponse, "/task_api_responses", task_response_cb, task_qos
            )

            # Subscribe to /fleet_states
            def fleet_callback(msg: FleetState):
                state.last_ros2_time = time.time()
                fleet_name = msg.name
                for r in msg.robots:
                    r_id = r.name
                    # Fuse Open-RMF task assignment and physical MQTT telemetry from discovered_robots
                    disc = (
                        state.discovered_robots.get(f"{fleet_name}_{r_id}")
                        or state.discovered_robots.get(f"fleet1_{r_id}")
                        or state.discovered_robots.get(f"fleet2_{r_id}")
                        or state.discovered_robots.get(r_id)
                    )
                    phys_status = disc.get("status") if disc else None

                    # If AMR reports moving physically, or RMF indicates mode 2, or has active task
                    if phys_status == "moving" or r.mode.mode == 2 or (r.task_id and phys_status != "idle"):
                        mode_str = "moving"
                    elif phys_status == "charging" or r.mode.mode == 1 or r.mode.mode == 3:
                        mode_str = "charging"
                    elif r.task_id:
                        mode_str = "moving"
                    else:
                        mode_str = "idle"

                    prev_robot = state.current_robots.get(r_id, {})
                    prev_task_id = prev_robot.get("task_id", "")

                    state.current_robots[r_id] = {
                        "id": r_id,
                        "name": r_id,
                        "fleet": fleet_name,
                        "x": round(float(r.location.x), 3),
                        "y": round(float(r.location.y), 3),
                        "yaw": round(float(r.location.yaw), 3),
                        "battery": round(float(r.battery_percent), 1),
                        "status": mode_str,
                        "current_task": r.task_id or ("正在導航" if mode_str == "moving" else "在線待命中"),
                        "task_id": r.task_id,
                    }

                    # Update tasks lifecycle in state.current_tasks
                    if r.task_id:
                        for t in state.current_tasks:
                            if t.get("id") == r.task_id or t.get("booking_id") == r.task_id:
                                t["status"] = "active"
                                t["robot_id"] = r_id
                                t["fleet_name"] = fleet_name
                                if mode_str == "moving":
                                    t["progress"] = min(95, max(15, t.get("progress", 0) + 2))
                                break
                    elif prev_task_id:
                        for t in state.current_tasks:
                            if (t.get("id") == prev_task_id or t.get("booking_id") == prev_task_id) and t.get("status") == "active":
                                t["status"] = "completed"
                                t["progress"] = 100
                                break

            self.ros_node.create_subscription(FleetState, "/fleet_states", fleet_callback, 10)

            # Subscribe to /task_summaries
            try:
                from rmf_task_msgs.msg import TaskSummary
                def task_summary_cb(ts: TaskSummary):
                    t_id = ts.task_profile.task_id or ts.task_id
                    status_map = {
                        0: "queued",
                        1: "active",
                        2: "completed",
                        3: "failed",
                        4: "canceled",
                        5: "queued",
                    }
                    stat = status_map.get(ts.state, "active")
                    for t in state.current_tasks:
                        if t.get("id") == t_id or t.get("booking_id") == t_id:
                            t["status"] = stat
                            if ts.robot_name:
                                t["robot_id"] = ts.robot_name
                            if ts.fleet_name:
                                t["fleet_name"] = ts.fleet_name
                            if stat == "completed":
                                t["progress"] = 100
                            break
                self.ros_node.create_subscription(TaskSummary, "/task_summaries", task_summary_cb, 10)
            except Exception as e:
                print(f"[RMF Bridge] Note: TaskSummary sub setup: {e}")


            # Start spinning in a background daemon thread
            spin_thread = threading.Thread(target=lambda: rclpy.spin(self.ros_node), daemon=True)
            spin_thread.start()
            print("[RMF Bridge] ✅ ROS 2 Bridge Node initialized and spinning.")
        except Exception as e:
            print(f"[RMF Bridge ERROR] Failed to initialize ROS 2 node: {e}")

    def dispatch_task(
        self,
        task_type: str,
        target_waypoint: str,
        destination_waypoint: Optional[str] = None,
        robot_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Dispatches a task to Open-RMF Task Dispatcher via ROS 2 ApiRequest.
        Open-RMF will evaluate bids, assign to winning fleet adapter,
        and fleet adapter will dispatch down to AMR via MQTT.
        """
        req_id = f"task_{int(time.time() * 1000) % 100000:05d}"
        
        # Build places sequence
        places = [target_waypoint]
        if destination_waypoint and destination_waypoint != target_waypoint:
            places.append(destination_waypoint)

        # Determine target fleet if robot_id specified
        target_fleet = None
        if robot_id and robot_id != "any":
            rob = state.current_robots.get(robot_id)
            if rob:
                target_fleet = rob.get("fleet")
            else:
                disc = state.discovered_robots.get(f"fleet1_{robot_id}") or state.discovered_robots.get(f"fleet2_{robot_id}")
                if disc:
                    target_fleet = disc.get("fleet_name")
            if not target_fleet:
                target_fleet = "fleet1" if "1" in robot_id else ("fleet2" if "2" in robot_id else "tinyRobot")

        # Map UI task types to Open-RMF category
        # Open-RMF standard accepts "patrol" (which works for single-place goto as well as multi-point patrol/loop)
        category = "patrol"
        description: Dict[str, Any] = {
            "places": places,
            "rounds": 1,
        }

        # Format Request JSON
        if robot_id and robot_id != "any" and target_fleet:
            # Directed Task Request for specific robot
            payload_json = {
                "type": "robot_task_request",
                "robot": robot_id,
                "fleet": target_fleet,
                "request": {
                    "category": category,
                    "description": description,
                }
            }
        else:
            # Open-RMF Fleet Bidding Task Request (Optimal Auto Assignment)
            payload_json = {
                "type": "dispatch_task_request",
                "request": {
                    "category": category,
                    "description": description,
                }
            }

        # Add to state.current_tasks immediately
        new_task = {
            "id": req_id,
            "type": task_type,
            "target_waypoint": target_waypoint,
            "destination_waypoint": destination_waypoint,
            "robot_id": robot_id or "Auto (RMF)",
            "fleet_name": target_fleet or "Auto (RMF)",
            "status": "queued",
            "progress": 0,
            "created_at": time.strftime("%H:%M:%S"),
        }
        state.current_tasks.insert(0, new_task)

        # Publish to ROS 2 /task_api_requests
        if self.task_pub:
            msg = ApiRequest()
            msg.request_id = req_id
            msg.json_msg = json.dumps(payload_json)
            self.task_pub.publish(msg)
            print(f"[RMF Dispatcher] 🚀 Published ApiRequest [{req_id}] to Open-RMF: {payload_json}")
            append_adapter_log(
                target_fleet or "system",
                f"[INFO] [RMF Dispatcher]: Task [{req_id}] ({task_type} -> {places}) submitted to Open-RMF scheduler."
            )
            return {
                "status": "success",
                "task_id": req_id,
                "payload": payload_json,
                "message": f"任務 [{req_id}] 已成功提交至 Open-RMF 調度器！",
            }
        else:
            print(f"[RMF Dispatcher WARN] task_pub not available, task queued in local state.")
            return {
                "status": "warning",
                "task_id": req_id,
                "message": "ROS 2 Task Publisher 尚未就緒，任務暫時僅存於本地佇列。",
            }

    def shutdown(self):
        """Terminates processes gracefully."""
        self.running = False
        for proc, name in [
            (self.adapter_process, "mqtt_fleet_adapter"),
            (self.dispatcher_process, "rmf_task_dispatcher"),
            (self.schedule_process, "rmf_traffic_schedule"),
        ]:
            if proc:
                try:
                    proc.terminate()
                    proc.wait(timeout=2.0)
                    print(f"[RMF Core] Terminated {name}.")
                except Exception:
                    pass

rmf_service = RMFService()
