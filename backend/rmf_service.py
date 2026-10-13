import os
import sys
import json
import time
import uuid
import itertools
import asyncio
import subprocess
import threading
import math
from typing import Dict, Any, List, Optional, Tuple

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, QoSDurabilityPolicy, QoSReliabilityPolicy
from std_msgs.msg import String
from rmf_fleet_msgs.msg import FleetState
from rmf_task_msgs.msg import ApiRequest, ApiResponse, DispatchStates

import state
from controller.adapter_controller import append_adapter_log

_task_counter = itertools.count(1)

# Max distance (m) between a navigate step's x/y and the nav-graph waypoint it is mapped to for RMF.
COMPOSE_WAYPOINT_TOLERANCE_M = 0.5


def generate_task_id() -> str:
    """Generates guaranteed collision-free task ID: task_<timestamp_ms>_<counter>_<hex8>."""
    ts = int(time.time() * 1000)
    cnt = next(_task_counter)
    uid = uuid.uuid4().hex[:8]
    return f"task_{ts}_{cnt}_{uid}"

def task_matches(task: Dict[str, Any], identifier: str) -> bool:
    """Matches task by booking_id, request_id, or local id."""
    if not identifier:
        return False
    return (
        task.get("booking_id") == identifier
        or task.get("request_id") == identifier
        or task.get("id") == identifier
    )

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
        self.fleet_control_pub = None
        self.robot_control_pub = None
        self.task_cmd_pub = None
        self.discovered_sub = None
        self.running = False
        # request_id -> {"event": threading.Event, "responses": [payload, ...]} for Task API calls awaiting a reply
        self.pending_requests: Dict[str, Dict[str, Any]] = {}
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
                if self.adapter_process.poll() is not None:
                    append_adapter_log("system", "Fleet Adapter 啟動後立即退出，請檢查 /tmp/adapter.log。")
                    return False
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
                    with self.lock:
                        pending = self.pending_requests.get(req_id)
                        if pending is not None:
                            pending["responses"].append(payload)
                            pending["event"].set()
                    if pending is not None:
                        return
                    
                    is_success = payload.get("success", True)
                    errors = payload.get("errors") or []
                    
                    if not is_success or errors:
                        err_str = "; ".join(errors) if isinstance(errors, list) else str(errors)
                        if not err_str:
                            err_str = "Task request rejected by Open-RMF dispatcher"
                        print(f"[RMF Task API] Request [{req_id}] REJECTED: {err_str}")
                        for t in state.current_tasks:
                            if task_matches(t, req_id):
                                t["status"] = "failed"
                                t["error"] = err_str
                                t["detail"] = err_str
                                append_adapter_log(
                                    t.get("fleet_name") or "system",
                                    f"[ERROR] Task [{req_id}] rejected by RMF: {err_str}",
                                )
                                break
                    else:
                        state_obj = payload.get("state")
                        if isinstance(state_obj, dict):
                            booking_id = state_obj.get("booking", {}).get("id") or req_id
                            rmf_status = state_obj.get("status", "queued")
                            status_map = {
                                "queued": "queued",
                                "standby": "queued",
                                "underway": "active",
                                "completed": "completed",
                                "failed": "failed",
                                "canceled": "canceled",
                                "cancelled": "canceled",
                                "killed": "failed",
                            }
                            mapped_status = status_map.get(rmf_status, "queued")
                            for t in state.current_tasks:
                                if task_matches(t, req_id):
                                    if booking_id:
                                        t["booking_id"] = booking_id
                                    # Terminal state protection: do not let non-terminal override terminal
                                    if t.get("status") not in state.TERMINAL_STATES or mapped_status in state.TERMINAL_STATES:
                                        t["status"] = mapped_status
                                        if mapped_status == "completed":
                                            t["progress"] = 100
                                    break
                except Exception as ex:
                    print(f"[RMF Task API] Error parsing response: {ex}")

            self.ros_node.create_subscription(
                ApiResponse, "/task_api_responses", task_response_cb, task_qos
            )

            # Subscribe to /dispatch_states
            try:
                from rmf_task_msgs.msg import DispatchStates
                def dispatch_states_cb(msg: DispatchStates):
                    for ds in list(msg.active) + list(msg.finished):
                        tid = ds.task_id
                        # 4: STATUS_FAILED_TO_ASSIGN, 5: STATUS_CANCELED_IN_FLIGHT
                        if ds.status == 4:
                            err_msg = "; ".join(ds.errors) if ds.errors else "Open-RMF 派車失敗 (無可用車輛或無解)"
                            for t in state.current_tasks:
                                if task_matches(t, tid):
                                    if t.get("status") not in state.TERMINAL_STATES:
                                        t["status"] = "failed"
                                        t["error"] = err_msg
                                        t["detail"] = err_msg
                                        append_adapter_log(
                                            t.get("fleet_name") or "system",
                                            f"[ERROR] Task [{tid}] dispatch failed: {err_msg}",
                                        )
                        elif ds.status == 5:
                            for t in state.current_tasks:
                                if task_matches(t, tid):
                                    if t.get("status") not in state.TERMINAL_STATES:
                                        t["status"] = "canceled"
                                        append_adapter_log(
                                            t.get("fleet_name") or "system",
                                            f"[WARN] Task [{tid}] canceled in flight",
                                        )
                        elif ds.assignment and ds.assignment.is_assigned:
                            for t in state.current_tasks:
                                if task_matches(t, tid):
                                    if ds.assignment.expected_robot_name:
                                        t["robot_id"] = ds.assignment.expected_robot_name
                                    if ds.assignment.fleet_name:
                                        t["fleet_name"] = ds.assignment.fleet_name
                self.ros_node.create_subscription(DispatchStates, "/dispatch_states", dispatch_states_cb, 10)
            except Exception as e:
                print(f"[RMF Bridge] Note: DispatchStates sub setup: {e}")

            # Subscribe to /fleet_states
            def fleet_callback(msg: FleetState):
                state.last_ros2_time = time.time()
                fleet_name = msg.name
                for r in msg.robots:
                    r_id = r.name
                    prev_robot = state.current_robots.get(r_id, {})
                    prev_task_id = prev_robot.get("task_id", "")
                    # RMF keeps reporting the last known pose for a disconnected robot; the FA knows the link state.
                    is_offline = state.is_robot_offline(fleet_name, r_id)
                    is_estopped = state.is_robot_estopped(fleet_name, r_id)

                    if is_estopped:
                        mode_str = "emergency_stop"
                    elif is_offline:
                        mode_str = "offline"
                    elif r.mode.mode == 3 or fleet_name in state.paused_fleets:
                        mode_str = "paused"
                    elif r.mode.mode == 2 or r.task_id:
                        mode_str = "moving"
                    elif r.mode.mode == 1:
                        mode_str = "charging"
                    else:
                        mode_str = "idle"

                    # Keep the previous task id while offline so completion is only inferred after reconnecting.
                    task_id = r.task_id or (prev_task_id if is_offline else "")
                    is_off_graph, off_dist = state.is_robot_off_graph(fleet_name, r_id, float(r.location.x), float(r.location.y))
                    if is_estopped:
                        current_task = f"緊急停車（{task_id}）" if task_id else "緊急停車"
                    elif is_offline:
                        current_task = f"離線（{task_id}）" if task_id else "離線"
                    elif is_off_graph:
                        mode_str = "off_graph"
                        current_task = f"偏離路網 ({off_dist}m) · 禁止派工"
                    else:
                        current_task = r.task_id or ("正在導航" if mode_str == "moving" else "已暫停" if mode_str == "paused" else "在線待命中")

                    state.current_robots[r_id] = {
                        "id": r_id,
                        "name": r_id,
                        "fleet": fleet_name,
                        "x": round(float(r.location.x), 3),
                        "y": round(float(r.location.y), 3),
                        "yaw": round(float(r.location.yaw), 3),
                        "battery": round(float(r.battery_percent), 1),
                        "status": mode_str,
                        "current_task": current_task,
                        "task_id": task_id,
                        "emergency_stop": is_estopped,
                        "online": not is_offline,
                        "is_off_graph": is_off_graph,
                        "off_graph_distance": off_dist,
                    }

                    if is_offline:
                        continue

                    # Update tasks lifecycle in state.current_tasks
                    if r.task_id:
                        for t in state.current_tasks:
                            if task_matches(t, r.task_id):
                                # Terminal state protection: never revert terminated task back to active
                                if t.get("status") not in state.TERMINAL_STATES:
                                    t["status"] = "active"
                                    t["robot_id"] = r_id
                                    t["fleet_name"] = fleet_name
                                    if mode_str == "moving":
                                        t["progress"] = min(95, max(15, t.get("progress", 0) + 2))
                                break
                        # If robot transitioned directly to a new task, complete the previous task
                        if prev_task_id and prev_task_id != r.task_id:
                            for t in state.current_tasks:
                                if task_matches(t, prev_task_id):
                                    if t.get("status") not in state.TERMINAL_STATES and not t.get("cancel_requested"):
                                        t["status"] = "completed"
                                        t["progress"] = 100
                                        append_adapter_log(
                                            fleet_name,
                                            f"[INFO] Task [{prev_task_id}] completed by robot [{r_id}].",
                                        )
                                    break
                    elif prev_task_id:
                        # Robot had an active task and now r.task_id is empty (execution completed).
                        # Guard: only mark completed if robot is not in emergency/error.
                        if r.mode.mode not in (5, 8):
                            for t in state.current_tasks:
                                if task_matches(t, prev_task_id):
                                    if t.get("status") not in state.TERMINAL_STATES and not t.get("cancel_requested"):
                                        t["status"] = "completed"
                                        t["progress"] = 100
                                        t["robot_id"] = r_id
                                        t["fleet_name"] = fleet_name
                                        append_adapter_log(
                                            fleet_name,
                                            f"[INFO] Task [{prev_task_id}] successfully completed by robot [{r_id}].",
                                        )
                                    break
                    else:
                        # Fallback recovery: confirm completion for tasks that were running on this robot and robot is now idle
                        if mode_str == "idle" and r.mode.mode not in (5, 8):
                            for t in state.current_tasks:
                                if (
                                    t.get("robot_id") == r_id
                                    and t.get("status") == "active"
                                    and not t.get("cancel_requested")
                                    and t.get("progress", 0) >= 90
                                ):
                                    t["status"] = "completed"
                                    t["progress"] = 100
                                    append_adapter_log(
                                        fleet_name,
                                        f"[INFO] Task [{t.get('booking_id') or t.get('id')}] confirmed completed by robot [{r_id}].",
                                    )

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
                        if task_matches(t, t_id):
                            # Terminal state protection
                            if t.get("status") in state.TERMINAL_STATES and stat not in state.TERMINAL_STATES:
                                break
                            t["status"] = stat
                            if ts.robot_name:
                                t["robot_id"] = ts.robot_name
                            if ts.fleet_name:
                                t["fleet_name"] = ts.fleet_name
                            if stat == "completed":
                                t["progress"] = 100
                            elif stat in ["failed", "canceled"]:
                                if ts.status:
                                    t["error"] = ts.status
                                    t["detail"] = ts.status
                            break
                self.ros_node.create_subscription(TaskSummary, "/task_summaries", task_summary_cb, task_qos)
            except Exception as e:
                print(f"[RMF Bridge] Note: TaskSummary sub setup: {e}")


            # Fleet control publisher
            self.fleet_control_pub = self.ros_node.create_publisher(
                String, "/rmf/fleet_control", 10
            )

            # Robot control publisher (operator emergency stop / release)
            self.robot_control_pub = self.ros_node.create_publisher(
                String, "/rmf/robot_control", 10
            )

            # Multi-step task command publisher to Fleet Adapter
            self.task_cmd_pub = self.ros_node.create_publisher(
                String, "/rmf/task_command", 10
            )

            # Subscribe to multi-step task status published by Fleet Adapter
            def task_status_cb(msg: String):
                try:
                    data = json.loads(msg.data)
                    r_id = data.get("robot_id")
                    f_name = data.get("fleet_name")
                    cmd_id = data.get("cmd_id")
                    stat = data.get("status")
                    step = data.get("step", 0)
                    steps_count = data.get("steps", 0)
                    msg_text = data.get("message", "")
                    t_id = data.get("task_id")

                    for t in state.current_tasks:
                        matched = task_matches(t, t_id) if t_id else (
                            cmd_id is not None and t.get("robot_id") == r_id and t.get("cmd_id") == cmd_id)
                        if matched:
                            if cmd_id is not None:
                                t["cmd_id"] = cmd_id
                            t["current_step"] = step
                            t["total_steps"] = steps_count
                            if steps_count > 0:
                                t["progress"] = min(100, max(0, round(((step + (1 if stat in ("waiting", "succeeded") else 0.5)) / steps_count) * 100)))

                            if stat in ("dispatched", "running"):
                                if t.get("status") not in state.TERMINAL_STATES and t.get("status") != "canceling":
                                    t["status"] = "active"
                                    t["detail"] = f"步驟 {step + 1}/{steps_count} 執行中..."
                            elif stat == "waiting":
                                if t.get("status") not in state.TERMINAL_STATES and t.get("status") != "canceling":
                                    t["status"] = "active"
                                    t["detail"] = f"步驟 {step + 1}/{steps_count} 完成，等待放行..."
                            elif stat == "held":
                                if t.get("status") not in state.TERMINAL_STATES:
                                    t["status"] = "active"
                                    t["detail"] = f"急停中斷，解除後從步驟 {step + 1}/{steps_count} 續行"
                            elif stat == "canceling":
                                if t.get("status") not in state.TERMINAL_STATES:
                                    t["status"] = "canceling"
                                    t["detail"] = "取消中，等待車端確認"
                            elif stat == "succeeded":
                                t["status"] = "completed"
                                t["progress"] = 100
                                t["detail"] = f"全部 {steps_count} 步驟已完成"
                                append_adapter_log(f_name or "system", f"[INFO] Multi-step task [{t.get('id')}] succeeded on {r_id}.")
                            elif stat in ["aborted", "rejected"]:
                                t["status"] = "failed"
                                t["error"] = msg_text or "步驟執行失敗"
                                t["detail"] = f"步驟 {step + 1} 失敗: {msg_text}"
                                append_adapter_log(f_name or "system", f"[ERROR] Multi-step task [{t.get('id')}] failed on {r_id}: {msg_text}")
                            elif stat == "canceled":
                                t["status"] = "canceled"
                                t["detail"] = "已取消"
                            break
                except Exception as ex:
                    print(f"[RMF Bridge] Error updating task status: {ex}")

            self.task_status_sub = self.ros_node.create_subscription(
                String, "/rmf/task_status", task_status_cb, 10
            )

            # Subscribe to discovered robots published by Fleet Adapter
            def discovered_cb(msg: String):
                try:
                    data = json.loads(msg.data)
                    if isinstance(data, dict):
                        state.discovered_robots.clear()
                        state.discovered_robots.update(data)
                        state.sync_robot_specs_to_adapters(data)
                except Exception as e:
                    print(f"[RMF Bridge] Error updating discovered robots: {e}")

            self.discovered_sub = self.ros_node.create_subscription(
                String, "/rmf/discovered_robots", discovered_cb, 10
            )

            # Fleet Adapter runtime state (latched): MQTT link + paused fleets (ACK for pause/resume)
            def fa_state_cb(msg: String):
                try:
                    data = json.loads(msg.data)
                    if isinstance(data, dict):
                        state.update_fa_state(data)
                except Exception as e:
                    print(f"[RMF Bridge] Error updating fleet adapter state: {e}")

            fa_state_qos = QoSProfile(
                depth=1,
                reliability=QoSReliabilityPolicy.RELIABLE,
                durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
            )
            self.fa_state_sub = self.ros_node.create_subscription(
                String, "/rmf/fleet_adapter_state", fa_state_cb, fa_state_qos
            )

            # Start spinning in a background daemon thread
            spin_thread = threading.Thread(target=lambda: rclpy.spin(self.ros_node), daemon=True)
            spin_thread.start()
            print("[RMF Bridge] ✅ ROS 2 Bridge Node initialized and spinning.")
        except Exception as e:
            print(f"[RMF Bridge ERROR] Failed to initialize ROS 2 node: {e}")

    def _publish_until_confirmed(self, pub, topic: str, payload: Dict[str, Any], confirmed, timeout_s: float) -> bool:
        """Publish to the FA (resending every second) until its latched state satisfies `confirmed()`.

        Blocking; call from a worker thread.
        """
        if not pub or not rclpy.ok():
            print(f"[RMF Bridge ERROR] Publisher for {topic} not ready.")
            return False
        data = json.dumps(payload)
        deadline = time.time() + timeout_s
        next_publish = 0.0
        while time.time() < deadline:
            if state.is_fa_state_fresh() and confirmed():
                print(f"[RMF Bridge] Fleet Adapter confirmed {data}.")
                return True
            if time.time() >= next_publish:
                try:
                    msg = String()
                    msg.data = data
                    pub.publish(msg)
                    print(f"[RMF Bridge] Published to {topic}: {data}")
                except Exception as e:
                    print(f"[RMF Bridge ERROR] Failed to publish to {topic}: {e}")
                    return False
                next_publish = time.time() + 1.0
            time.sleep(0.1)
        print(f"[RMF Bridge ERROR] Fleet Adapter did not confirm {data} within {timeout_s}s.")
        return False

    def send_fleet_control(self, fleet_name: str, action: str, timeout_s: float = 4.0) -> bool:
        """Fleet pause/resume via the FA. The FA owns paused_fleets.json, so nothing is persisted here."""
        if action not in ("pause", "resume"):
            return False
        want_paused = action == "pause"
        return self._publish_until_confirmed(
            self.fleet_control_pub, "/rmf/fleet_control",
            {"action": action, "fleet": fleet_name},
            lambda: (fleet_name in state.paused_fleets) == want_paused,
            timeout_s,
        )

    def send_robot_control(self, fleet_name: str, robot_id: str, action: str, timeout_s: float = 4.0) -> bool:
        """Operator emergency stop ('estop') / 'release' of one robot via the FA (owner of estopped_robots.json)."""
        if action not in ("estop", "release"):
            return False
        want_estopped = action == "estop"
        return self._publish_until_confirmed(
            self.robot_control_pub, "/rmf/robot_control",
            {"action": action, "fleet": fleet_name, "robot": robot_id},
            lambda: state.is_robot_estopped(fleet_name, robot_id) == want_estopped,
            timeout_s,
        )

    def _call_task_api(self, payload_json: Dict[str, Any], timeout_s: float, grace_s: float = 1.0) -> List[Dict[str, Any]]:
        """Publish a Task API request and collect its responses. Blocking; call from a worker thread.

        Several fleet adapters may answer the same request (only the owner of a task succeeds), so after the
        first non-successful reply we keep listening for `grace_s` for a successful one.
        """
        if not self.task_pub or not rclpy.ok():
            raise RuntimeError("ROS 2 Task Publisher 尚未就緒")
        req_id = f"api_{uuid.uuid4().hex[:12]}"
        pending = {"event": threading.Event(), "responses": []}
        with self.lock:
            self.pending_requests[req_id] = pending
        try:
            msg = ApiRequest()
            msg.request_id = req_id
            msg.json_msg = json.dumps(payload_json)
            self.task_pub.publish(msg)
            print(f"[RMF Task API] Published [{req_id}]: {payload_json}")
            deadline = time.time() + timeout_s
            while time.time() < deadline:
                pending["event"].wait(max(0.0, deadline - time.time()))
                pending["event"].clear()
                with self.lock:
                    responses = list(pending["responses"])
                if any(r.get("success") for r in responses):
                    break
                if responses:
                    deadline = min(deadline, time.time() + grace_s)
            with self.lock:
                return list(pending["responses"])
        finally:
            with self.lock:
                self.pending_requests.pop(req_id, None)

    def cancel_task(self, identifier: str, timeout_s: float = 5.0) -> Dict[str, Any]:
        """Cancels a queued or in-progress task via the Open-RMF Task API (cancel_task_request).

        The owning fleet adapter removes a queued task from the robot's queue, or stops the active one
        (RMF then sends 'stop' to the robot through the MQTT Fleet Adapter). Blocking; call from a worker thread.
        """
        task = next((t for t in state.current_tasks if task_matches(t, identifier)), None)
        if task is not None:
            if task.get("status") in state.TERMINAL_STATES:
                return {"status": "error", "task_id": identifier,
                        "message": f"任務已結束 ({task.get('status')})，無法取消。"}
            if task.get("type") == "multi_step":
                # The robot confirms the cancel (task_status 'canceled') once the running step has stopped.
                task["cancel_requested"] = True
                task["status"] = "canceling"
                task["detail"] = "取消中，等待車端確認"
                if self.task_cmd_pub and rclpy.ok():
                    try:
                        msg = String()
                        msg.data = json.dumps({
                            "action": "stop",
                            "fleet_name": task.get("fleet_name"),
                            "robot_id": task.get("robot_id"),
                            "task_id": task.get("id"),
                        })
                        self.task_cmd_pub.publish(msg)
                    except Exception as ex:
                        print(f"[RMF Dispatcher WARN] Failed to send stop for multi-step task: {ex}")
                append_adapter_log(
                    task.get("fleet_name") or "system",
                    f"[WARN] Multi-step task [{identifier}] cancel requested by operator.",
                )
                return {"status": "success", "task_id": identifier, "message": f"多步任務 [{identifier}] 取消中，等待車端確認。"}
            rmf_task_id = task.get("booking_id")
            if not rmf_task_id:
                return {"status": "error", "task_id": identifier,
                        "message": "任務尚未取得 Open-RMF 任務編號（仍在派車競標中），請稍後再試。"}
        else:
            # Not tracked by this backend (e.g. after a backend restart): treat it as an RMF task id.
            rmf_task_id = identifier

        if task is not None:
            task["cancel_requested"] = True
        try:
            responses = self._call_task_api(
                {"type": "cancel_task_request", "task_id": rmf_task_id, "labels": ["rmf_web_studio"]}, timeout_s)
        except Exception as e:
            if task is not None:
                task.pop("cancel_requested", None)
            return {"status": "error", "task_id": rmf_task_id, "message": f"無法送出取消請求: {e}"}

        if any(r.get("success") for r in responses):
            if task is not None and task.get("status") not in state.TERMINAL_STATES:
                task["status"] = "canceled"
                task["detail"] = "已由操作員取消"
            append_adapter_log(
                (task or {}).get("fleet_name") or "system",
                f"[WARN] [RMF Dispatcher]: Task [{rmf_task_id}] canceled by operator.",
            )
            return {"status": "success", "task_id": rmf_task_id, "message": f"任務 [{rmf_task_id}] 已取消。"}

        if task is not None:
            task.pop("cancel_requested", None)
        errors = []
        for r in responses:
            for err in r.get("errors") or []:
                errors.append(err.get("detail") or err.get("category") or str(err) if isinstance(err, dict) else str(err))
        message = "; ".join(errors) if errors else "Open-RMF 未回應取消請求（任務可能不存在或已結束）。"
        return {"status": "error", "task_id": rmf_task_id, "message": f"取消失敗: {message}"}

    @staticmethod
    def _unknown_places_for_adapter(places: List[str], fleet: Optional[str]) -> Optional[str]:
        """Error message if a place is missing from the graphs the Fleet Adapter actually loaded.

        Open-RMF would otherwise reject the task with "No fleet adapters offered a bid".
        """
        loaded = state.loaded_fleet_waypoints()
        if loaded is None:
            return None
        if fleet and fleet not in loaded:
            return f"車隊 '{fleet}' 尚未被 Fleet Adapter 載入，請至車隊管理頁檢查設定並重新載入 Adapter"
        candidates = [fleet] if fleet else [f for f in loaded if f not in state.paused_fleets]
        if any(all(p in loaded[f] for p in places) for f in candidates):
            return None
        missing = [p for p in places if not any(p in loaded[f] for f in candidates)]
        scope = f"車隊 '{fleet}'" if fleet else "任何可用車隊"
        if missing:
            msg = f"站點 {', '.join(repr(p) for p in missing)} 不在{scope}的 Fleet Adapter 已載入路網中"
        else:
            msg = "沒有同一個可用車隊的已載入路網涵蓋全部任務站點，請檢查取貨／卸貨或巡邏站點所屬路網"
        stale = [
            a.get("fleet_name", "")
            for a in state.load_fleet_adapters_data()
            if (not fleet or a.get("fleet_name") == fleet) and state.adapter_diagnostics(a).get("status") == "stale"
        ]
        if stale:
            msg += f"；車隊 {', '.join(stale)} 的路網已更新但 Adapter 尚未重新載入，請至車隊管理頁按「重新載入」"
        return msg

    @staticmethod
    def _resolve_dispatch_target(robot_id: Optional[str], fleet_name: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        """Resolve the target fleet for an RMF task and check it can take work. Returns (fleet, error)."""
        target_fleet = fleet_name
        if not target_fleet and robot_id and robot_id != "any":
            rob = state.current_robots.get(robot_id)
            if rob:
                target_fleet = rob.get("fleet")
            else:
                disc = state.discovered_robots.get(f"fleet1_{robot_id}") or state.discovered_robots.get(f"fleet2_{robot_id}")
                if disc:
                    target_fleet = disc.get("fleet_name")
            if not target_fleet:
                target_fleet = "fleet1" if "1" in robot_id else ("fleet2" if "2" in robot_id else "tinyRobot")

        if target_fleet and target_fleet in state.paused_fleets:
            print(f"[RMF Dispatcher WARN] Fleet [{target_fleet}] is paused, rejecting task dispatch.")
            return target_fleet, f"車隊 '{target_fleet}' 目前處於暫停狀態 (paused)，無法受理調度任務。請先至車隊管理介面恢復車隊。"

        if robot_id and robot_id != "any" and target_fleet and state.is_robot_estopped(target_fleet, robot_id):
            return target_fleet, f"車輛 '{robot_id}' 處於緊急停車狀態，請先解除緊急停車。"

        if robot_id and robot_id != "any":
            is_off, off_dist = state.is_robot_off_graph(target_fleet or "", robot_id)
            if is_off:
                return target_fleet, f"車輛 '{robot_id}' 偏離路網過遠 ({off_dist}m > 3.0m)，無法操控接單！請手動移車或在路網編輯器中將路網連至車輛位置。"
        else:
            # Auto dispatch: check if all active robots are off-graph
            online_robots = [
                r for r in state.current_robots.values()
                if (not target_fleet or r.get("fleet") == target_fleet)
                and r.get("online", True)
                and not r.get("emergency_stop", False)
            ]
            if online_robots and all(state.is_robot_off_graph(r.get("fleet", ""), r.get("id"))[0] for r in online_robots):
                scope_str = f"車隊 '{target_fleet}' 內" if target_fleet else "目前系統中"
                return target_fleet, f"{scope_str}所有在線車輛皆偏離路網過遠 (> 3.0m)，無法操控接單！請先手動移車或在路網編輯器中將路網連過去。"
        return target_fleet, None

    @staticmethod
    def _active_map_waypoints() -> Optional[List[Dict[str, Any]]]:
        if not os.path.exists(state.ACTIVE_MAP_FILE):
            return None
        try:
            with open(state.ACTIVE_MAP_FILE, "r", encoding="utf-8") as mf:
                return [w for w in json.load(mf).get("waypoints", []) if w.get("name")]
        except Exception as e:
            print(f"[RMF Dispatcher WARN] Cannot read active map waypoints: {e}")
            return None

    @classmethod
    def steps_to_compose_phases(cls, steps: Any) -> Tuple[List[Dict[str, Any]], List[str], Optional[str]]:
        """Convert multi-step task steps into Open-RMF compose phases. Returns (phases, places, error).

        - navigate -> go_to_place: RMF plans the route and negotiates traffic, so it alone releases
          each navigation. The step needs a nav-graph waypoint: 'place', or x/y that lies on one.
        - gimbal_move / gimbal_rotate -> perform_action: executed by the fleet adapter's action
          executor, which sends the robot a gimbal_move / gimbal_rotate command.
        """
        if not isinstance(steps, list) or not steps:
            return [], [], "多步任務必須包含至少一個步驟 (steps)"
        waypoints = cls._active_map_waypoints()
        names = {w["name"] for w in waypoints} if waypoints else None
        phases: List[Dict[str, Any]] = []
        places: List[str] = []
        for i, step in enumerate(steps):
            label = f"步驟 {i + 1}"
            if not isinstance(step, dict):
                return [], [], f"{label} 格式錯誤，必須為物件"
            stype = step.get("type")
            try:
                if stype == "navigate":
                    place = step.get("place") or step.get("waypoint")
                    if place is None:
                        if "x" not in step or "y" not in step:
                            return [], [], f"{label} navigate 需指定站點 'place' 或座標 x/y"
                        if not waypoints:
                            return [], [], f"{label} 無法以座標對應站點：目前沒有導航路網"
                        x, y = float(step["x"]), float(step["y"])
                        nearest = min(waypoints, key=lambda w: math.hypot(float(w["x"]) - x, float(w["y"]) - y))
                        dist = math.hypot(float(nearest["x"]) - x, float(nearest["y"]) - y)
                        if dist > COMPOSE_WAYPOINT_TOLERANCE_M:
                            return [], [], (f"{label} 座標 ({x:.2f}, {y:.2f}) 不在路網站點上（最近站點 "
                                            f"'{nearest['name']}' 相距 {dist:.2f}m）；RMF 只能導航到路網站點")
                        place = nearest["name"]
                    place = str(place).strip()
                    if names is not None and place not in names:
                        return [], [], f"{label} 站點 '{place}' 不存在於目前導航地圖中"
                    destination: Dict[str, Any] = {"waypoint": place}
                    if step.get("yaw") is not None:
                        destination["orientation"] = float(step["yaw"])
                    activity = {"category": "go_to_place", "description": destination}
                    places.append(place)
                elif stype in ("gimbal_move", "gimbal_rotate"):
                    activity = {
                        "category": "perform_action",
                        "description": {
                            "unix_millis_action_duration_estimate": int(float(step.get("duration_s", 10.0)) * 1000),
                            "category": stype,
                            "description": {
                                "pan_deg": float(step.get("pan_deg", 0.0)),
                                "tilt_deg": float(step.get("tilt_deg", 0.0)),
                            },
                            "use_tool_sink": False,
                        },
                    }
                else:
                    return [], [], f"{label} 不支援的步驟類型 '{stype}'（可用：navigate、gimbal_move、gimbal_rotate）"
            except (TypeError, ValueError) as e:
                return [], [], f"{label} 參數格式錯誤: {e}"
            phases.append({"activity": {"category": "sequence", "description": {"activities": [activity]}}})
        return phases, places, None

    def dispatch_compose_task(
        self,
        steps: List[Dict[str, Any]],
        robot_id: Optional[str] = None,
        fleet_name: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Submit a multi-step task (navigate / gimbal) to Open-RMF as one compose task.

        Unlike dispatch_multi_step_task (sent straight to the robot), RMF schedules every
        navigation phase against the traffic of the other robots. Without robot_id RMF picks the robot.
        """
        req_id = task_id or generate_task_id()
        phases, places, err = self.steps_to_compose_phases(steps)
        if err:
            return {"status": "error", "task_id": None, "request_id": None, "message": err}

        robot_id = robot_id if robot_id and robot_id != "any" else None
        target_fleet, err = self._resolve_dispatch_target(robot_id, fleet_name)
        if err:
            return {"status": "error", "task_id": None, "request_id": None, "message": err}

        if places:
            unreachable = self._unknown_places_for_adapter(places, target_fleet if robot_id else None)
            if unreachable:
                return {"status": "error", "task_id": None, "request_id": None, "message": unreachable}

        if not self.task_pub or not rclpy.ok():
            return {
                "status": "error",
                "task_id": req_id,
                "request_id": req_id,
                "message": "ROS 2 Task Publisher 尚未就緒，無法受理調度請求。請確認 RMF 核心服務已啟動。",
            }

        request: Dict[str, Any] = {
            "category": "compose",
            "description": {"category": "multi_step", "phases": phases},
            "requester": "rmf_web_studio",
        }
        if robot_id and target_fleet:
            payload_json = {"type": "robot_task_request", "robot": robot_id, "fleet": target_fleet, "request": request}
        else:
            if target_fleet:
                request["fleet_name"] = target_fleet
            payload_json = {"type": "dispatch_task_request", "request": request}

        new_task = {
            "id": req_id,
            "request_id": req_id,
            "booking_id": None,
            "type": "compose",
            "target_waypoint": " → ".join(
                s.get("place") or s.get("waypoint") or s.get("type", "?") if isinstance(s, dict) else "?" for s in steps),
            "robot_id": robot_id or "Auto (RMF)",
            "fleet_name": target_fleet or "Auto (RMF)",
            "status": "queued",
            "progress": 0,
            "total_steps": len(steps),
            "steps": steps,
            "created_at": time.strftime("%H:%M:%S"),
            "detail": f"共 {len(steps)} 個步驟，由 Open-RMF 排程",
        }
        state.current_tasks.insert(0, new_task)

        msg = ApiRequest()
        msg.request_id = req_id
        msg.json_msg = json.dumps(payload_json)
        self.task_pub.publish(msg)
        print(f"[RMF Dispatcher] Published compose ApiRequest [{req_id}] to Open-RMF: {msg.json_msg}")
        append_adapter_log(
            target_fleet or "system",
            f"[INFO] [RMF Dispatcher]: Compose task [{req_id}] ({len(steps)} steps) submitted to Open-RMF scheduler.",
        )
        return {
            "status": "success",
            "task_id": req_id,
            "request_id": req_id,
            "payload": payload_json,
            "message": f"多步任務 [{req_id}] 已提交至 Open-RMF 調度器（compose）！",
        }

    def dispatch_task(
        self,
        task_type: str,
        target_waypoint: str,
        destination_waypoint: Optional[str] = None,
        robot_id: Optional[str] = None,
        fleet_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Dispatches a task to Open-RMF Task Dispatcher via ROS 2 ApiRequest.
        Open-RMF will evaluate bids, assign to winning fleet adapter,
        and fleet adapter will dispatch down to AMR via MQTT.
        """
        req_id = generate_task_id()
        
        # 1. Validate task_type
        valid_types = {"goto", "patrol", "delivery"}
        if task_type not in valid_types:
            return {
                "status": "error",
                "task_id": None,
                "request_id": None,
                "message": f"未知任務類型: '{task_type}'，支援類型為: {', '.join(sorted(valid_types))}",
            }

        # 2. Validate target_waypoint
        if not target_waypoint or not isinstance(target_waypoint, str) or not target_waypoint.strip():
            return {
                "status": "error",
                "task_id": None,
                "request_id": None,
                "message": "任務缺少必要目標站點 (target_waypoint)",
            }
        target_waypoint = target_waypoint.strip()

        # 3. Validate destination_waypoint for delivery
        if destination_waypoint:
            destination_waypoint = destination_waypoint.strip()
            if not destination_waypoint:
                destination_waypoint = None

        if task_type == "delivery":
            if not destination_waypoint:
                return {
                    "status": "error",
                    "task_id": None,
                    "request_id": None,
                    "message": "物料配送 (delivery) 任務必須指定卸貨站點 (destination_waypoint)",
                }
            if destination_waypoint == target_waypoint:
                return {
                    "status": "error",
                    "task_id": None,
                    "request_id": None,
                    "message": "物料配送 (delivery) 取貨站點與卸貨站點不能相同",
                }

        # 4. Validate waypoints against active navigation graph if available
        if os.path.exists(state.ACTIVE_MAP_FILE):
            try:
                with open(state.ACTIVE_MAP_FILE, "r", encoding="utf-8") as mf:
                    map_json = json.load(mf)
                    wps = map_json.get("waypoints", [])
                    if wps:
                        valid_names = {w.get("name") for w in wps if w.get("name")} | {w.get("id") for w in wps if w.get("id")}
                        if target_waypoint not in valid_names:
                            return {
                                "status": "error",
                                "task_id": None,
                                "request_id": None,
                                "message": f"無效站點: 目標站點 '{target_waypoint}' 不存在於目前導航地圖中",
                            }
                        if destination_waypoint and destination_waypoint not in valid_names:
                            return {
                                "status": "error",
                                "task_id": None,
                                "request_id": None,
                                "message": f"無效站點: 目的站點 '{destination_waypoint}' 不存在於目前導航地圖中",
                            }
            except Exception as e:
                print(f"[RMF Dispatcher WARN] Map waypoint validation warning: {e}")

        # 5. Format payload category and description according to verified semantics
        if task_type == "delivery":
            category = "delivery"
            description = {
                "pickup": {
                    "place": target_waypoint,
                    "handler": f"dispenser_{target_waypoint}",
                    "payload": [],
                },
                "dropoff": {
                    "place": destination_waypoint,
                    "handler": f"ingestor_{destination_waypoint}",
                    "payload": [],
                },
            }
            places = [target_waypoint, destination_waypoint]
        elif task_type == "patrol":
            category = "patrol"
            places = [target_waypoint]
            if destination_waypoint and destination_waypoint != target_waypoint:
                places.append(destination_waypoint)
            description = {
                "places": places,
                "rounds": 1,
            }
        else: # goto
            category = "patrol"
            places = [target_waypoint]
            description = {
                "places": places,
                "rounds": 1,
            }

        target_fleet, target_err = self._resolve_dispatch_target(robot_id, fleet_name)
        if target_err:
            return {
                "status": "error",
                "task_id": None,
                "request_id": None,
                "message": target_err,
            }

        unreachable = self._unknown_places_for_adapter(places, target_fleet if robot_id and robot_id != "any" else None)
        if unreachable:
            return {
                "status": "error",
                "task_id": None,
                "request_id": None,
                "message": unreachable,
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

        # Check if publisher is ready before accepting or enqueueing task
        if not self.task_pub or not rclpy.ok():
            print(f"[RMF Dispatcher ERROR] task_pub not available, rejecting task request [{req_id}].")
            return {
                "status": "error",
                "task_id": req_id,
                "request_id": req_id,
                "message": "ROS 2 Task Publisher 尚未就緒，無法受理調度請求。請確認 RMF 核心服務已啟動。",
            }

        # Add to state.current_tasks immediately
        new_task = {
            "id": req_id,
            "request_id": req_id,
            "booking_id": None,
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
            "request_id": req_id,
            "payload": payload_json,
            "message": f"任務 [{req_id}] 已成功提交至 Open-RMF 調度器！",
        }

    def dispatch_multi_step_task(
        self,
        robot_id: str,
        steps: List[Dict[str, Any]],
        fleet_name: Optional[str] = None,
        auto_proceed: bool = False,
        step_delay: float = 0.0,
        task_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Dispatches a multi-step task directly to the Fleet Adapter via ROS 2.
        By default each step waits for an operator proceed (auto_proceed=False)."""
        if not robot_id or robot_id == "any":
            return {"status": "error", "message": "多步任務必須指定特定車輛 (robot_id)"}
        if not steps or not isinstance(steps, list):
            return {"status": "error", "message": "多步任務必須包含至少一個步驟 (steps)"}

        req_id = task_id or generate_task_id()
        target_fleet = fleet_name
        if not target_fleet:
            rob = state.current_robots.get(robot_id)
            if rob:
                target_fleet = rob.get("fleet")
            else:
                target_fleet = state.find_robot_fleet(robot_id)
        if not target_fleet:
            target_fleet = "fleet1" if "1" in robot_id else ("fleet2" if "2" in robot_id else "tinyRobot")

        if not self.task_cmd_pub or not rclpy.ok():
            return {
                "status": "error",
                "task_id": req_id,
                "message": "ROS 2 Task Command Publisher 尚未就緒，無法下派多步任務。",
            }

        new_task = {
            "id": req_id,
            "request_id": req_id,
            "booking_id": req_id,
            "type": "multi_step",
            "target_waypoint": f"{len(steps)} steps",
            "robot_id": robot_id,
            "fleet_name": target_fleet,
            "status": "queued",
            "progress": 0,
            "total_steps": len(steps),
            "current_step": 0,
            "steps": steps,
            "auto_proceed": auto_proceed,
            "step_delay": step_delay,
            "created_at": time.strftime("%H:%M:%S"),
            "detail": f"共 {len(steps)} 個步驟下派中",
        }
        state.current_tasks.insert(0, new_task)

        msg = String()
        msg.data = json.dumps({
            "action": "dispatch_task",
            "fleet_name": target_fleet,
            "robot_id": robot_id,
            "steps": steps,
            "auto_proceed": auto_proceed,
            "step_delay": step_delay,
            "task_id": req_id,
        })
        self.task_cmd_pub.publish(msg)
        print(f"[RMF Dispatcher] 🚀 Published multi-step task [{req_id}] to /rmf/task_command: {msg.data}")
        append_adapter_log(
            target_fleet,
            f"[INFO] Multi-step task [{req_id}] ({len(steps)} steps) dispatched to {robot_id}."
        )
        return {
            "status": "success",
            "task_id": req_id,
            "request_id": req_id,
            "message": f"多步任務 [{req_id}] 已下派至車輛 {robot_id}！",
        }

    def proceed_task(self, robot_id: str, fleet_name: Optional[str] = None, cmd_id: Optional[int] = None) -> Dict[str, Any]:
        """Dispatches 'proceed' action to release the next step for a waiting multi-step task."""
        target_fleet = fleet_name or state.find_robot_fleet(robot_id) or "fleet1"
        if not self.task_cmd_pub or not rclpy.ok():
            return {"status": "error", "message": "ROS 2 Task Command Publisher 尚未就緒"}
        msg = String()
        msg.data = json.dumps({
            "action": "proceed",
            "fleet_name": target_fleet,
            "robot_id": robot_id,
            "cmd_id": cmd_id,
        })
        self.task_cmd_pub.publish(msg)
        print(f"[RMF Dispatcher] ⏩ Published proceed to /rmf/task_command for {robot_id}")
        return {"status": "success", "message": f"已發送 proceed 放行指令給車輛 {robot_id}"}

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
