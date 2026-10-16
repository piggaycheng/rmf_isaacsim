#!/usr/bin/env python3
# ==============================================================================
# Open-RMF MQTT Fleet Adapter (EasyFullControl)
# Connects AMR fleets via MQTT and bridges them to the Open-RMF core scheduler.
# ==============================================================================

import argparse
import hashlib
import json
import math
import os
import sys
import threading
import time
import uuid
from typing import Dict, List, Optional, Any, Set, Tuple

import numpy as np
import paho.mqtt.client as mqtt
import yaml

# ROS 2 & Open-RMF imports
try:
    import rclpy
    from rclpy.node import Node
    from rclpy.parameter import Parameter
    from rclpy.qos import QoSProfile, QoSDurabilityPolicy, QoSReliabilityPolicy
    from std_msgs.msg import String
    from rmf_task_msgs.msg import ApiRequest, ApiResponse
    import rmf_adapter
    from rmf_adapter import Adapter
    import rmf_adapter.easy_full_control as rmf_easy
    HAS_RMF = True
except ImportError as e:
    HAS_RMF = False
    print(f"[WARN] Open-RMF / ROS 2 libraries not found: {e}")
    print("[WARN] Running in standalone mock mode only.")


def find_backend_path(subpath: str) -> str:
    candidates = [
        os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../backend", subpath)),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "../../backend", subpath)),
        os.path.abspath(os.path.join("/root/rmf_ws/backend", subpath)),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    for c in candidates:
        if os.path.isdir(os.path.dirname(c)):
            return c
    return candidates[0]


def nav_graph_fingerprint(path: str) -> Optional[str]:
    """Content hash of a nav graph file, so the backend can tell whether the loaded graph is outdated."""
    try:
        with open(path, "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()
    except OSError as e:
        print(f"[WARN] Cannot fingerprint nav graph '{path}': {e}")
        return None


# Grace period before a dispatched command not reflected in heartbeats is considered lost.
LOST_CMD_RESEND_S = 5.0

# Level used when neither the robot nor the nav graph names one (matches the robot's default map).
DEFAULT_LEVEL = "1F"


def rmf_to_robot(transform: Optional[Dict[str, Any]], x: float, y: float) -> Tuple[float, float]:
    """RMF building frame -> robot map frame (same convention as the fleet config ``transforms``)."""
    if not transform:
        return x, y
    c, s = math.cos(transform.get("rotation", 0.0)), math.sin(transform.get("rotation", 0.0))
    k = transform.get("scale", 1.0)
    tx, ty = transform.get("translation", [0.0, 0.0])
    return tx + k * (c * x - s * y), ty + k * (s * x + c * y)


def robot_to_rmf(transform: Optional[Dict[str, Any]], x: float, y: float) -> Tuple[float, float]:
    if not transform:
        return x, y
    c, s = math.cos(transform.get("rotation", 0.0)), math.sin(transform.get("rotation", 0.0))
    k = transform.get("scale", 1.0)
    dx, dy = x - transform.get("translation", [0.0, 0.0])[0], y - transform.get("translation", [0.0, 0.0])[1]
    return (c * dx + s * dy) / k, (-s * dx + c * dy) / k


def transform_pose_between_levels(
    src_transform: Optional[Dict[str, Any]], dst_transform: Optional[Dict[str, Any]], pose: Any
) -> Tuple[float, float, float]:
    """Robot map pose on the source floor -> robot map pose on the destination floor (via the RMF frame).
    Valid for a robot inside a lift cabin, which keeps its building-frame pose while the lift moves."""
    bx, by = robot_to_rmf(src_transform, float(pose[0]), float(pose[1]))
    x, y = rmf_to_robot(dst_transform, bx, by)
    yaw = float(pose[2]) - (src_transform or {}).get("rotation", 0.0) + (dst_transform or {}).get("rotation", 0.0)
    return x, y, math.atan2(math.sin(yaw), math.cos(yaw))


# The robot pose recorded at prepare_floor_change, carried to the target floor, must land inside the cabin
# RMF localizes to; a larger gap means the level transforms or the recorded pose are wrong.
LIFT_ENTRY_POSE_MAX_OFFSET_M = 1.5


def prepare_floor_change_payload(
    robot_id: str, cmd_id: int, lift_name: str = "", floor_name: str = "", position: Any = None
) -> Dict[str, Any]:
    """Robot-side MQTT extension: freeze localization and pause Nav2 inside a lift cabin before the lift moves."""
    payload: Dict[str, Any] = {
        "robot_id": robot_id,
        "cmd_id": cmd_id,
        "action": "prepare_floor_change",
        "timestamp": time.time(),
    }
    if lift_name:
        payload["lift_name"] = lift_name
    if floor_name:
        payload["floor_name"] = floor_name
    if position is not None:
        payload["pose"] = {"x": float(position[0]), "y": float(position[1]), "yaw": float(position[2])}
    return payload


def change_floor_payload(robot_id: str, cmd_id: int, floor_name: str, position: Any) -> Dict[str, Any]:
    """Robot-side MQTT extension: reload the target floor map and relocalize at a planar pose (robot map frame)."""
    return {
        "robot_id": robot_id,
        "cmd_id": cmd_id,
        "action": "change_floor",
        "floor_name": floor_name,
        "initial_pose": {"x": float(position[0]), "y": float(position[1]), "yaw": float(position[2])},
        "timestamp": time.time(),
    }


# A robot farther than this from the nav graph is off-graph (no bidding / commands). Matches RMF's
# max merge distance below, beyond which RMF cannot snap the robot onto the graph.
OFF_GRAPH_THRESHOLD_M = 5.0

# A navigate to an ordinary waypoint finishes once a heartbeat is this close to the target, so the
# robot flows through intermediate waypoints instead of fine-aligning its heading at each one.
# Precise waypoints (lift cabins, docks, chargers, workcells, or marked precise_arrival) wait for
# the robot's command_result instead.
ARRIVAL_DISTANCE_M = 0.25
ARRIVAL_YAW_RAD = 0.20


class ManagedRobot:
    """Tracks state and command execution handles for a single robot."""

    def __init__(
        self,
        name: str,
        map_name: str,
        position: np.ndarray,
        battery_soc: float,
        update_handle: Any = None,
        charger_waypoint: str = "charger_1",
        fleet_name: str = "tinyRobot",
    ):
        self.name = name
        self.fleet_name = fleet_name
        self.map_name = map_name
        self.position = position  # [x, y, yaw]
        self.battery_soc = battery_soc  # 0.0 - 1.0
        self.update_handle = update_handle
        self.charger_waypoint = charger_waypoint
        self.status = "idle"
        self.last_heartbeat = time.time()
        self.heartbeat_received = False
        self.is_online = True
        self.is_off_graph = False
        self.off_graph_distance = 0.0

        # Navigation & Activity Execution State
        self.cmd_id = 0
        self.stop_cmd_id: Optional[int] = None
        self.current_execution = None
        self.target_position: Optional[np.ndarray] = None
        self.is_holding: bool = False
        self.is_paused: bool = False
        # Operator emergency stop: robot halted, decommissioned, and RMF commands held until released.
        self.is_estopped: bool = False
        self.is_retrying: bool = False
        self.nav_retries: int = 0
        self.last_cmd_payload: Optional[Dict[str, Any]] = None
        # RMF command received while offline; held (not sent) until the robot is back online.
        self.deferred_cmd_payload: Optional[Dict[str, Any]] = None
        self.last_dispatch_time: float = 0.0
        # Lift state: when navigating into a lift cabin, pending_suspend_lift holds the lift name so FA
        # dispatches prepare_floor_change before finishing the RMF boarding execution.
        self.pending_suspend_lift: Optional[str] = None
        self.nav_suspended: bool = False
        # (level, [x, y, yaw]) the robot reported when it froze localization in the lift; seeds change_floor.
        self.lift_entry_pose: Optional[Tuple[str, np.ndarray]] = None
        # False when the active navigate targets an ordinary waypoint that heartbeats may complete.
        self.precise_arrival: bool = True
        # Multi-Step Task Tracking (per multi_step_task.md specification)
        self.active_task: Optional[Dict[str, Any]] = None
        # cmd_id -> backend task info, kept until the robot reports a terminal state so that a
        # task superseded by RMF still reports its final state to the backend.
        self.task_refs: Dict[Any, Dict[str, Any]] = {}
        self.session_id: Optional[str] = None
        self.session_reset_pending = False
        self.session_reset_error: Optional[str] = None
        self.session_ready = threading.Event()
        self.session_reset_lock = threading.Lock()
        self.session_cleanup_at = 0.0


def validate_task_steps(steps: Any) -> Tuple[bool, str, List[Dict[str, Any]]]:
    """Validates multi-step task steps per multi_step_task.md specification.
    
    Supported types:
      - navigate: x (float), y (float), yaw (float, optional default 0.0)
      - gimbal_move: pan_deg (float), tilt_deg (float)
      - gimbal_rotate: pan_deg (float, optional default 0.0, |pan_deg| <= 180), tilt_deg (float, optional default 0.0)
    Planned but not implemented (rejected per spec):
      - rotate, take_photo
    """
    if not isinstance(steps, list) or not steps:
        return False, "'steps' must be a non-empty list", []
    validated: List[Dict[str, Any]] = []
    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            return False, f"Step {idx} must be a dictionary", []
        step_type = step.get("type")
        if not step_type:
            return False, f"Step {idx} missing 'type'", []

        if step_type == "navigate":
            if "x" not in step or "y" not in step:
                return False, f"Step {idx} ('navigate') requires 'x' and 'y'", []
            try:
                x = float(step["x"])
                y = float(step["y"])
                yaw = float(step.get("yaw", 0.0))
                if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(yaw)):
                    return False, f"Step {idx} ('navigate') coordinates must be finite numbers", []
            except (ValueError, TypeError):
                return False, f"Step {idx} ('navigate') coordinates must be numbers", []
            validated.append({"type": "navigate", "x": x, "y": y, "yaw": yaw})

        elif step_type == "gimbal_move":
            if "pan_deg" not in step or "tilt_deg" not in step:
                return False, f"Step {idx} ('gimbal_move') requires 'pan_deg' and 'tilt_deg'", []
            try:
                pan = float(step["pan_deg"])
                tilt = float(step["tilt_deg"])
                if not (math.isfinite(pan) and math.isfinite(tilt)):
                    return False, f"Step {idx} ('gimbal_move') angles must be finite numbers", []
            except (ValueError, TypeError):
                return False, f"Step {idx} ('gimbal_move') angles must be numbers", []
            validated.append({"type": "gimbal_move", "pan_deg": pan, "tilt_deg": tilt})

        elif step_type == "gimbal_rotate":
            try:
                pan = float(step.get("pan_deg", 0.0))
                tilt = float(step.get("tilt_deg", 0.0))
                if not (math.isfinite(pan) and math.isfinite(tilt)):
                    return False, f"Step {idx} ('gimbal_rotate') angles must be finite numbers", []
                if abs(pan) > 180.0:
                    return False, f"Step {idx} ('gimbal_rotate') |pan_deg| must be <= 180", []
            except (ValueError, TypeError):
                return False, f"Step {idx} ('gimbal_rotate') angles must be numbers", []
            validated.append({"type": "gimbal_rotate", "pan_deg": pan, "tilt_deg": tilt})

        elif step_type in ("rotate", "take_photo"):
            return False, f"Step {idx} ('{step_type}') is planned in protocol but not implemented on robot", []
        else:
            return False, f"Step {idx} has unknown type '{step_type}'", []

    return True, "", validated


class MqttFleetAdapter:
    """Open-RMF Fleet Adapter that interfaces with AMRs over MQTT."""

    def __init__(
        self,
        config_path: str,
        nav_graph_path: str,
        mqtt_host: str = "127.0.0.1",
        mqtt_port: int = 1883,
        mock_rmf: bool = False,
    ):
        self.config_path = os.path.abspath(config_path)
        self.nav_graph_path = os.path.abspath(nav_graph_path)
        self.mqtt_host = mqtt_host
        self.mqtt_port = mqtt_port
        self.mock_rmf = mock_rmf or (not HAS_RMF)

        # 1. Load YAML Configuration
        with open(self.config_path, "r", encoding="utf-8") as f:
            self.config_yaml = yaml.safe_load(f)

        self.fleet_name = self.config_yaml.get("rmf_fleet", {}).get("name", "tinyRobot")
        mqtt_cfg = self.config_yaml.get("mqtt", {})
        self.topic_prefix = mqtt_cfg.get("topic_prefix", "rmf")
        self.keepalive = mqtt_cfg.get("keepalive", 30)

        self.robots: Dict[Tuple[str, str], ManagedRobot] = {}
        self.discovered_robots: Dict[str, Dict[str, Any]] = {}
        self.lock = threading.Lock()
        self.running = True
        self.configured_fleets: Set[str] = set()
        self.paused_fleets: Set[str] = set()
        self.estopped_robots: Set[str] = set()  # "{fleet}/{robot}"
        self.fleet_waypoint_info: Dict[str, Dict[str, Any]] = {}
        # "{fleet}_{robot}" -> messages about configured charger/parking waypoints missing from the graph
        self.waypoint_warnings: Dict[str, List[str]] = {}
        self.started_at = time.time()
        self.session_api_requests: Dict[str, Dict[str, Any]] = {}
        self.sessions_path = find_backend_path("saved_maps/robot_sessions.json")
        self.robot_sessions: Dict[str, Dict[str, Any]] = {}
        if os.path.exists(self.sessions_path):
            with open(self.sessions_path, encoding="utf-8") as f:
                self.robot_sessions = json.load(f)
            if not isinstance(self.robot_sessions, dict) or any(
                not isinstance(record, dict)
                or not isinstance(record.get("session_id"), str)
                or not record["session_id"]
                or not isinstance(record.get("retired_ids", []), list)
                or any(not isinstance(value, str) for value in record.get("retired_ids", []))
                for record in self.robot_sessions.values()
            ):
                raise ValueError("Invalid robot_sessions.json; refusing to discard session history")

        estop_file = find_backend_path("saved_maps/estopped_robots.json")
        if os.path.exists(estop_file):
            try:
                with open(estop_file, "r", encoding="utf-8") as f:
                    self.estopped_robots = set(json.load(f))
            except Exception as e:
                print(f"[WARN] Failed to read estopped_robots.json: {e}")

        # Load paused fleets if available
        paused_file = find_backend_path("saved_maps/paused_fleets.json")
        if os.path.exists(paused_file):
            try:
                with open(paused_file, "r", encoding="utf-8") as f:
                    self.paused_fleets = set(json.load(f))
            except Exception as e:
                print(f"[WARN] Failed to read paused_fleets.json: {e}")

        # Load discovered robots cache if available
        disc_file = find_backend_path("saved_maps/discovered_robots.json")
        if os.path.exists(disc_file):
            try:
                with open(disc_file, "r", encoding="utf-8") as f:
                    self.discovered_robots = json.load(f)
            except Exception as e:
                pass

        # Pre-populate configured_fleets from fleet_adapters.json or fallback
        adapters_file = find_backend_path("saved_maps/fleet_adapters.json")
        adapters_data = []
        if os.path.exists(adapters_file):
            try:
                with open(adapters_file, "r", encoding="utf-8") as f:
                    adapters_data = json.load(f)
            except Exception as e:
                print(f"[WARN] Failed to read fleet_adapters.json: {e}")

        if not adapters_data:
            adapters_data = [{"fleet_name": self.fleet_name, "graph_idx": 0}]

        self.configured_fleets = {item.get("fleet_name") or self.fleet_name for item in adapters_data}

        # ROS 2 Pub/Sub handles (assigned when RMF is live)
        self.discovered_pub = None
        self.fa_state_pub = None
        self.fleet_control_sub = None
        self.robot_control_sub = None
        self.mqtt_connected = False

        print(f"==================================================")
        print(f"🚀 Initializing MQTT Fleet Adapter: [{self.fleet_name}]")
        print(f"   Config:    {self.config_path}")
        print(f"   Nav Graph: {self.nav_graph_path}")
        print(f"   MQTT:      {self.mqtt_host}:{self.mqtt_port}")
        print(f"   Mode:      {'MOCK / STANDALONE' if self.mock_rmf else 'LIVE OPEN-RMF'}")
        print(f"   Configured Fleets: {list(self.configured_fleets)}")
        print(f"==================================================")

        # 2. Initialize Open-RMF EasyFullControl (if not in mock mode)
        self.adapter = None
        self.fleet_handle = None
        self.fleet_handles: Dict[str, Any] = {}
        self.node = None
        if not self.mock_rmf:
            self._init_rmf()

        # 3. Initialize MQTT Client
        client_id = f"rmf_fleet_adapter_{self.fleet_name}_{int(time.time())}"
        self.mqtt_client = mqtt.Client(client_id=client_id)
        self.mqtt_client.on_connect = self._on_mqtt_connect
        self.mqtt_client.on_message = self._on_mqtt_message
        self.mqtt_client.on_disconnect = self._on_mqtt_disconnect

    def _get_robot(self, fleet_name: Optional[str], robot_id: str) -> Optional[ManagedRobot]:
        target_fleet = fleet_name or self.fleet_name
        return self.robots.get((target_fleet, robot_id))

    def _publish_command(self, robot: ManagedRobot, payload: Dict[str, Any]):
        if robot.session_reset_pending and payload.get("action") != "stop":
            print(f"[SESSION WARN] Command blocked during reset for '{robot.fleet_name}/{robot.name}'")
            return
        if robot.session_id:
            payload = dict(payload, session_id=robot.session_id)
        topic = f"{self.topic_prefix}/{robot.fleet_name}/robot/{robot.name}/command"
        self.mqtt_client.publish(topic, json.dumps(payload), qos=1)

    def _fleet_velocity_limits(
        self, fleet_config: Dict[str, Any], linear: float, angular: float
    ) -> Tuple[float, float]:
        fleet_name = fleet_config.get("fleet_name") or self.fleet_name
        robot_names = {r["name"] for r in fleet_config.get("robots", [])}
        limits = [linear, angular]
        for robot in self.discovered_robots.values():
            if robot.get("fleet_name") != fleet_name:
                continue
            if robot_names and robot.get("robot_id") not in robot_names:
                continue
            specs = robot.get("specs", {})
            for index, key in enumerate(("max_linear_velocity", "max_angular_velocity")):
                if key not in specs:
                    continue
                value = float(specs[key])
                if not math.isfinite(value) or value <= 0:
                    raise ValueError(
                        f"Invalid {key} for {fleet_name}/{robot.get('robot_id')}: {specs[key]!r}"
                    )
                limits[index] = min(limits[index], value)
        if tuple(limits) != (linear, angular):
            print(
                f"[RMF WARN] Fleet [{fleet_name}] planning velocity capped to robot-reported limits: "
                f"linear {linear} -> {limits[0]} m/s, angular {angular} -> {limits[1]} rad/s"
            )
        return limits[0], limits[1]

    def _sync_rmf_availability(self, robot: ManagedRobot):
        """Single source of truth for RMF status/commission: offline > e-stop > paused > available."""
        if not robot.update_handle:
            return
        try:
            more = robot.update_handle.more()
            if more is None:
                return
            if robot.session_reset_pending:
                commission = more.commission()
                commission.accept_dispatched_tasks = False
                commission.accept_direct_tasks = False
                commission.perform_idle_behavior = False
                more.set_commission(commission)
                more.override_status("error" if robot.session_reset_error else None)
            elif robot.session_id:
                available = robot.is_online and not robot.is_estopped and not robot.is_paused
                commission = more.commission()
                commission.accept_dispatched_tasks = available
                commission.accept_direct_tasks = available
                commission.perform_idle_behavior = not robot.is_estopped and not robot.is_paused
                more.set_commission(commission)
                more.override_status("offline" if not robot.is_online else "error" if robot.is_estopped else None)
            elif not robot.is_online:
                more.override_status("offline")
                more.unstable_decommission()
            elif robot.is_estopped:
                more.override_status("error")
                more.unstable_decommission()
            elif robot.is_paused:
                # "paused" is not a valid robot_state.json status; decommission alone blocks new bids.
                more.override_status(None)
                more.unstable_decommission()
            else:
                more.override_status(None)
                more.unstable_recommission()
        except Exception as e:
            print(f"[RMF WARN] Error syncing RMF availability for '{robot.name}' ({robot.fleet_name}): {e}")

    def _defer_command(self, robot: ManagedRobot, execution: Any, cmd_payload: Dict[str, Any], err_msg: str):
        """Hold an RMF command for an offline/e-stopped robot instead of rejecting/replanning (avoids replan loops)."""
        if robot.session_reset_pending:
            print(f"[SESSION] Discarded old-session command for '{robot.name}' during reset")
            return
        print(f"[RMF -> AMR WARN] {err_msg} Deferring until robot is back online and not e-stopped.")
        robot.is_holding = False
        robot.cmd_id += 1  # invalidate late results from earlier commands
        robot.nav_retries = 0
        robot.is_retrying = False
        robot.current_execution = execution
        robot.target_position = None
        robot.last_cmd_payload = None
        robot.deferred_cmd_payload = cmd_payload
        if robot.update_handle:
            try:
                robot.update_handle.more().log_error(f"{err_msg} Command deferred until robot is available.")
            except Exception as e:
                print(f"[RMF WARN] Error logging deferred command for '{robot.name}': {e}")
        self._sync_rmf_availability(robot)

    def _resend_if_lost(self, robot: ManagedRobot, reported_cmd_id: Any):
        """Commands published while the robot's MQTT link was down (before the FA noticed) are lost
        with a clean session. If heartbeats still do not show the active cmd_id after a grace period,
        resend it with the same cmd_id (robots must ignore a duplicate of the running cmd_id)."""
        payload = robot.last_cmd_payload
        if (
            robot.current_execution is None
            or robot.session_reset_pending
            or payload is None
            or robot.deferred_cmd_payload is not None
            or robot.is_retrying
            or payload.get("cmd_id") != robot.cmd_id
            or reported_cmd_id == robot.cmd_id
        ):
            return
        now = time.time()
        if now - robot.last_dispatch_time < LOST_CMD_RESEND_S:
            return
        robot.last_dispatch_time = now
        cmd_topic = f"{self.topic_prefix}/{robot.fleet_name}/robot/{robot.name}/command"
        self._publish_command(robot, dict(payload, timestamp=now))
        print(f"[RMF -> AMR] Robot '{robot.name}' does not report cmd_id {robot.cmd_id} (reports {reported_cmd_id}); resent {payload.get('action')} via {cmd_topic}")

    def _dispatch_deferred_command(self, robot: ManagedRobot):
        payload = robot.deferred_cmd_payload
        if payload is None or robot.is_estopped or not robot.is_online or robot.session_reset_pending:
            return
        robot.deferred_cmd_payload = None
        execution = robot.current_execution
        if execution is None or not execution.okay():
            print(f"[RMF -> AMR] Dropping deferred command for '{robot.name}': RMF execution no longer active.")
            robot.current_execution = None
            return
        if payload.get("action") == "task":
            res = self._dispatch_multi_step_task_locked(
                robot, payload["steps"], execution=execution,
                auto_proceed=payload.get("auto_proceed", True), step_delay=payload.get("step_delay", 0.0),
                task_id=payload.get("task_id"), step_offset=payload.get("step_offset", 0),
                all_steps=payload.get("all_steps"))
            if res["status"] == "error":
                self._escalate_rmf_failure(robot, f"Deferred task for '{robot.name}' could not be dispatched: {res['message']}")
            return
        robot.cmd_id += 1
        payload = dict(payload, cmd_id=robot.cmd_id, timestamp=time.time())
        target = payload.get("target")
        if payload.get("action") in ("navigate", "dock") and target:
            robot.target_position = np.array(
                [target["x"], target["y"], target["yaw"]], dtype=np.float64)
        robot.last_cmd_payload = payload
        robot.last_dispatch_time = time.time()
        cmd_topic = f"{self.topic_prefix}/{robot.fleet_name}/robot/{robot.name}/command"
        self._publish_command(robot, payload)
        print(f"[RMF -> AMR] Dispatched deferred {payload.get('action')} (cmd_id: {robot.cmd_id}) to '{robot.name}' via {cmd_topic}")

    def _save_robot_sessions(self):
        os.makedirs(os.path.dirname(self.sessions_path), exist_ok=True)
        tmp_path = f"{self.sessions_path}.{os.getpid()}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self.robot_sessions, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, self.sessions_path)

    def _session_matches(self, robot_id: str, fleet_name: str, payload: Dict[str, Any]) -> bool:
        robot = self._get_robot(fleet_name, robot_id)
        record = getattr(self, "robot_sessions", {}).get(f"{fleet_name}/{robot_id}", {})
        expected = robot.session_id if robot else record.get("session_id")
        if expected and payload.get("session_id") != expected:
            print(f"[SESSION WARN] Ignoring missing/stale session_id from '{fleet_name}/{robot_id}'")
            return False
        return True

    def _on_session_api_response(self, msg):
        try:
            response = json.loads(msg.json_msg)
        except (TypeError, ValueError) as e:
            print(f"[SESSION ERROR] Invalid RMF response: {e}")
            return
        with self.lock:
            pending = self.session_api_requests.get(msg.request_id)
            if pending is not None:
                pending["response"] = response
                pending["event"].set()

    def _call_session_api(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        request_id = f"session_{uuid.uuid4().hex}"
        pending = {"event": threading.Event()}
        with self.lock:
            self.session_api_requests[request_id] = pending
        try:
            msg = ApiRequest()
            msg.request_id = request_id
            msg.json_msg = json.dumps(payload)
            self.session_api_pub.publish(msg)
            if not pending["event"].wait(10.0):
                raise RuntimeError(f"RMF did not confirm {payload['type']}")
            response = pending["response"]
            if payload["type"] == "robot_commission_request":
                confirmed = all(
                    response.get(key, {}).get("success") is True
                    for key in ("commission", "pending_dispatch_tasks_policy", "pending_direct_tasks_policy")
                )
            else:
                confirmed = response.get("success") is True
            if not confirmed:
                raise RuntimeError(f"RMF rejected session cleanup: {response}")
            return response
        finally:
            with self.lock:
                self.session_api_requests.pop(request_id, None)

    def _reset_session(self, robot: ManagedRobot, session_id: str, reset_tasks: bool, started_at: float):
        with robot.session_reset_lock:
            if self.running and robot.session_id == session_id:
                self._reset_session_locked(robot, session_id, reset_tasks, started_at)

    def _reset_session_locked(self, robot: ManagedRobot, session_id: str, reset_tasks: bool, started_at: float):
        """Drain old work while fenced, then restore idle behavior from a fresh localized heartbeat."""
        def current():
            return self.running and robot.session_id == session_id

        commission_request = {
            "type": "robot_commission_request",
            "fleet": robot.fleet_name,
            "robot": robot.name,
            "commission": {"dispatch_tasks": False, "direct_tasks": False, "idle_behavior": False},
            "pending_dispatch_tasks_policy": "cancel",
            "pending_direct_tasks_policy": "cancel",
        }
        try:
            if not self.mock_rmf:
                deadline = time.monotonic() + 10.0
                while robot.update_handle.more() is None and current():
                    if time.monotonic() >= deadline:
                        raise RuntimeError("RMF robot handle did not become ready")
                    time.sleep(0.05)
                if not current():
                    return
                # Commissioning cancels idle behavior and drains both RMF queues without reassigning them.
                self._call_session_api(commission_request)
                if not current():
                    return
                task_id = robot.update_handle.more().current_task_id()
                if task_id and not task_id.startswith("wait."):
                    self._call_session_api({
                        "type": "kill_task_request", "task_id": task_id,
                        "labels": ["Vehicle started a new session"],
                    })
                # A second request is a worker-queue barrier: cancellation completion callbacks run
                # before we restore commissioning, so the old wait cannot suppress the new wait.
                self._call_session_api(commission_request)
            if not current():
                return
            if reset_tasks:
                with self.lock:
                    if not current():
                        return
                    robot.session_cleanup_at = started_at
                    self.robot_sessions[f"{robot.fleet_name}/{robot.name}"]["cleanup_at"] = started_at
                    self._save_robot_sessions()
                self._publish_fa_state()
            while current():
                if robot.session_ready.wait(0.5):
                    break
            with self.lock:
                if not current():
                    return
                if (
                    not robot.is_online or not robot.heartbeat_received or not robot.session_ready.is_set()
                    or time.time() - robot.last_heartbeat > 10.0
                ):
                    raise RuntimeError("Localization became unavailable during session reset; register again")
                if not self.mock_rmf:
                    robot.update_handle.update(
                        rmf_easy.RobotState(robot.map_name, robot.position, robot.battery_soc), None
                    )
                    robot.update_handle.more().enable_responsive_wait(
                        bool(self.config_yaml.get("rmf_fleet", {}).get("responsive_wait", True))
                    )
                record = self.robot_sessions[f"{robot.fleet_name}/{robot.name}"]
                record["reset_pending"] = False
                self._save_robot_sessions()
                robot.session_reset_pending = False
                robot.session_reset_error = None
                self._sync_rmf_availability(robot)
            print(f"[SESSION] '{robot.fleet_name}/{robot.name}' ready in session {session_id}")
        except Exception as e:
            with self.lock:
                if robot.session_id != session_id:
                    return
                robot.session_reset_pending = True
                robot.session_reset_error = str(e)
                self._sync_rmf_availability(robot)
            print(f"[SESSION ERROR] '{robot.fleet_name}/{robot.name}' remains blocked: {e}")
        self._publish_fa_state()

    def _start_session_reset(self, robot: ManagedRobot, reset_tasks: bool, started_at: float):
        robot.session_reset_pending = True
        robot.session_reset_error = None
        robot.session_ready.clear()
        self._detach_active_task_locked(robot, "vehicle started a new session")
        robot.task_refs.clear()
        robot.cmd_id += 1
        robot.current_execution = None
        robot.deferred_cmd_payload = None
        robot.last_cmd_payload = None
        robot.target_position = None
        robot.stop_cmd_id = None
        robot.is_holding = False
        robot.is_retrying = False
        robot.nav_retries = 0
        robot.pending_suspend_lift = None
        robot.nav_suspended = False
        robot.lift_entry_pose = None
        self._sync_rmf_availability(robot)
        threading.Thread(
            target=self._reset_session,
            args=(robot, robot.session_id, reset_tasks, started_at),
            daemon=True,
        ).start()


    def _enable_legacy_responsive_wait(self, robot: ManagedRobot):
        deadline = time.monotonic() + 10.0
        while self.running and not robot.session_id:
            more = robot.update_handle.more()
            if more is not None:
                with self.lock:
                    if not robot.session_id:
                        more.enable_responsive_wait(
                            bool(self.config_yaml.get("rmf_fleet", {}).get("responsive_wait", True))
                        )
                return
            if time.monotonic() >= deadline:
                print(f"[RMF ERROR] Cannot enable responsive wait for '{robot.fleet_name}/{robot.name}': "
                      "robot handle did not become ready")
                return
            time.sleep(0.05)

    @staticmethod
    def _point_to_segment_distance(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
        abx = bx - ax
        aby = by - ay
        seg_len_sq = abx * abx + aby * aby
        if seg_len_sq < 1e-9:
            return math.hypot(px - ax, py - ay)
        t = max(0.0, min(1.0, ((px - ax) * abx + (py - ay) * aby) / seg_len_sq))
        proj_x = ax + t * abx
        proj_y = ay + t * aby
        return math.hypot(px - proj_x, py - proj_y)

    def _compute_distance_to_graph(self, fleet_name: str, x: float, y: float, level: Optional[str] = None) -> float:
        """Distance from a robot pose (robot map frame) to the graph; with ``level`` only that floor counts."""
        f_info = self.fleet_waypoint_info.get(fleet_name, {})
        v_coords = f_info.get("vertex_coords", [])
        segments = f_info.get("lane_segments", [])
        levels = f_info.get("levels") or {}
        if level is not None and levels:
            if level not in levels:
                return float("inf")
            x, y = robot_to_rmf(levels[level]["transform"], x, y)
            v_coords = levels[level]["vertex_coords"]
            segments = levels[level]["lane_segments"]
        if not v_coords and not segments:
            coords = f_info.get("coords", {})
            v_coords = list(coords.values())
        if not v_coords and not segments:
            return float("inf")
        min_dist = float("inf")
        for vx, vy in v_coords:
            d = math.hypot(x - vx, y - vy)
            if d < min_dist:
                min_dist = d
        for (ax, ay), (bx, by) in segments:
            d = self._point_to_segment_distance(x, y, ax, ay, bx, by)
            if d < min_dist:
                min_dist = d
        return min_dist

    def _parse_nav_waypoints(self, nav_file: str):
        all_wps = []
        charger_wps = []
        parking_wps = []
        wp_coords: Dict[str, tuple] = {}
        all_vertex_coords: List[Tuple[float, float]] = []
        lane_segments: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
        # level -> {transform, vertex_coords, lane_segments, waypoints}; graph coordinates are RMF frame
        levels_info: Dict[str, Dict[str, Any]] = {}
        if os.path.exists(nav_file):
            try:
                with open(nav_file, "r", encoding="utf-8") as yf:
                    nav_dict = yaml.safe_load(yf) or {}
                for lvl_name, lvl_data in nav_dict.get("levels", {}).items():
                    raw_vertices = lvl_data.get("vertices", [])
                    v_positions = []
                    lvl_info = levels_info[str(lvl_name)] = {
                        "transform": lvl_data.get("transform") or {},
                        "vertex_coords": v_positions,
                        "lane_segments": [],
                        "waypoints": {},
                        "lift_waypoints": {},
                        "lift_cabins": [],
                        "precise_waypoints": set(),
                    }
                    for v in raw_vertices:
                        vx, vy = float(v[0]), float(v[1])
                        v_positions.append((vx, vy))
                        all_vertex_coords.append((vx, vy))
                        wp_name = None
                        is_chg = False
                        is_park = False
                        meta = {}
                        if len(v) >= 3 and isinstance(v[2], dict):
                            meta = v[2]
                            wp_name = meta.get("name")
                            is_chg = meta.get("is_charger", False)
                            is_park = meta.get("is_parking_spot", False)
                        elif len(v) >= 4 and isinstance(v[3], str):
                            wp_name = v[3]
                            meta = v[4] if len(v) >= 5 and isinstance(v[4], dict) else {}
                            is_chg = meta.get("is_charger", False)
                            is_park = meta.get("is_parking_spot", False)

                        lift_name = meta.get("lift") or ("lift_1" if meta.get("is_lift_cabin") else None)
                        if lift_name:
                            lvl_info["lift_cabins"].append((str(lift_name), vx, vy))

                        if wp_name:
                            all_wps.append(wp_name)
                            wp_coords[wp_name] = (vx, vy)
                            lvl_info["waypoints"][wp_name] = (vx, vy)
                            if lift_name:
                                lvl_info["lift_waypoints"][wp_name] = str(lift_name)
                            # Graphs built before precise_arrival existed: chargers and workcells.
                            precise = meta.get("precise_arrival") if "precise_arrival" in meta \
                                else (is_chg or meta.get("is_workcell"))
                            if lift_name or precise:
                                lvl_info["precise_waypoints"].add(wp_name)
                            if is_chg:
                                charger_wps.append(wp_name)
                            if is_park:
                                parking_wps.append(wp_name)

                    for lane in lvl_data.get("lanes", []):
                        if len(lane) >= 2:
                            idx1, idx2 = lane[0], lane[1]
                            if 0 <= idx1 < len(v_positions) and 0 <= idx2 < len(v_positions):
                                lane_segments.append((v_positions[idx1], v_positions[idx2]))
                                lvl_info["lane_segments"].append((v_positions[idx1], v_positions[idx2]))
            except Exception as e:
                print(f"[WARN] Failed to parse nav graph {nav_file}: {e}")
        self._last_parsed_levels = levels_info
        return all_wps, charger_wps, parking_wps, wp_coords, lane_segments, all_vertex_coords

    def _init_rmf(self):
        """Initializes ROS 2 and Open-RMF Adapter."""
        try:
            rclpy.init()
            rmf_adapter.init_rclcpp()

            self.node = rclpy.node.Node("rmf_mqtt_command_handle")
            task_qos = QoSProfile(
                depth=10, reliability=QoSReliabilityPolicy.RELIABLE,
                durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
            )
            self.session_api_pub = self.node.create_publisher(ApiRequest, "/task_api_requests", task_qos)
            self.session_api_sub = self.node.create_subscription(
                ApiResponse, "/task_api_responses", self._on_session_api_response, task_qos
            )
            self.discovered_pub = self.node.create_publisher(String, "/rmf/discovered_robots", 10)
            # Latched so a (re)started backend immediately learns paused fleets and MQTT link state.
            fa_state_qos = QoSProfile(
                depth=1,
                reliability=QoSReliabilityPolicy.RELIABLE,
                durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
            )
            self.fa_state_pub = self.node.create_publisher(String, "/rmf/fleet_adapter_state", fa_state_qos)
            self.fleet_control_sub = self.node.create_subscription(
                String, "/rmf/fleet_control", self._on_ros2_fleet_control, 10
            )
            self.robot_control_sub = self.node.create_subscription(
                String, "/rmf/robot_control", self._on_ros2_robot_control, 10
            )
            self.task_command_sub = self.node.create_subscription(
                String, "/rmf/task_command", self._on_ros2_task_command, 10
            )
            self.task_status_pub = self.node.create_publisher(
                String, "/rmf/task_status", 10
            )
            self.adapter = Adapter.make("rmf_mqtt_fleet_adapter")
            if not self.adapter:
                raise RuntimeError("Unable to initialize Adapter. Make sure RMF Schedule is running.")

            self.adapter.start()
            self.fleet_handles: Dict[str, Any] = {}

            # Load fleets from fleet_adapters.json if available
            adapters_file = find_backend_path("saved_maps/fleet_adapters.json")
            adapters_data = []
            if os.path.exists(adapters_file):
                try:
                    with open(adapters_file, "r", encoding="utf-8") as f:
                        adapters_data = json.load(f)
                except Exception as e:
                    print(f"[WARN] Failed to read fleet_adapters.json: {e}")

            if not adapters_data:
                adapters_data = [{"fleet_name": self.fleet_name, "graph_idx": 0}]

            self.fleet_waypoint_info: Dict[str, Dict[str, Any]] = {}

            for item in adapters_data:
                f_name = item.get("fleet_name") or self.fleet_name
                g_idx = item.get("graph_idx", 0)
                nav_path = find_backend_path(f"saved_maps/nav_graphs/{g_idx}.yaml")
                if not os.path.exists(nav_path):
                    nav_path = self.nav_graph_path

                all_wps, charger_wps, parking_wps, wp_coords, lane_segments, vertex_coords = self._parse_nav_waypoints(nav_path)

                self.fleet_waypoint_info[f_name] = {
                    "all": all_wps,
                    "chargers": charger_wps,
                    "parkings": parking_wps,
                    "coords": wp_coords,
                    "lane_segments": lane_segments,
                    "vertex_coords": vertex_coords,
                    "levels": self._last_parsed_levels,
                    "graph_idx": g_idx,
                    "graph_sha": nav_graph_fingerprint(nav_path),
                }

                try:
                    fleet_cfg_path = self.config_path
                    radius = 0.35
                    lin_v = 1.2
                    ang_v = 1.0
                    if os.path.exists(self.config_path):
                        with open(self.config_path, "r", encoding="utf-8") as f:
                            base_cfg_dict = yaml.safe_load(f)

                        radius = float(item.get("robot_radius") or base_cfg_dict.get("rmf_fleet", {}).get("profile", {}).get("footprint", 0.35))
                        lin_v = float(item.get("linear_velocity") or base_cfg_dict.get("rmf_fleet", {}).get("limits", {}).get("linear", [1.2, 0.6])[0])
                        ang_v = float(item.get("angular_velocity") or base_cfg_dict.get("rmf_fleet", {}).get("limits", {}).get("angular", [1.0, 0.5])[0])
                        lin_v, ang_v = self._fleet_velocity_limits(item, lin_v, ang_v)

                        base_cfg_dict["rmf_fleet"]["name"] = f_name
                        base_cfg_dict["rmf_fleet"]["limits"]["linear"] = [lin_v, round(lin_v * 0.6, 2)]
                        base_cfg_dict["rmf_fleet"]["limits"]["angular"] = [ang_v, round(ang_v * 0.6, 2)]
                        base_cfg_dict["rmf_fleet"]["profile"]["footprint"] = radius
                        base_cfg_dict["rmf_fleet"]["profile"]["vicinity"] = round(radius + 0.3, 2)
                        transforms = {
                            lvl: info["transform"]
                            for lvl, info in self.fleet_waypoint_info[f_name]["levels"].items()
                            if info["transform"]
                        }
                        if transforms:
                            base_cfg_dict["rmf_fleet"]["transforms"] = transforms

                        tmp_cfg_file = f"/tmp/fleet_cfg_{f_name}.yaml"
                        with open(tmp_cfg_file, "w", encoding="utf-8") as f:
                            yaml.dump(base_cfg_dict, f)
                        fleet_cfg_path = tmp_cfg_file

                    cfg = rmf_easy.FleetConfiguration.from_config_files(fleet_cfg_path, nav_path)
                    if cfg:
                        cfg.fleet_name = f_name
                        # New robots must not start idle navigation before their session is localized.
                        cfg.default_responsive_wait = False
                        cfg.default_max_merge_waypoint_distance = OFF_GRAPH_THRESHOLD_M
                        cfg.default_max_merge_lane_distance = OFF_GRAPH_THRESHOLD_M
                        handle = self.adapter.add_easy_fleet(cfg)
                        self.fleet_handles[f_name] = handle
                        self._setup_fleet_consideration(handle, f_name)
                        print(f"[RMF] Successfully registered fleet [{f_name}] (Footprint: {radius}m, LinVel: {lin_v}m/s, AngVel: {ang_v}rad/s, Graph {g_idx}) into Open-RMF!")
                except Exception as e:
                    print(f"[ERROR] Failed to register fleet [{f_name}]: {e}")

            if self.fleet_handles:
                self.fleet_handle = list(self.fleet_handles.values())[0]
                self.configured_fleets = set(self.fleet_handles.keys())
            else:
                self.fleet_config = rmf_easy.FleetConfiguration.from_config_files(self.config_path, self.nav_graph_path)
                self.fleet_config.default_responsive_wait = False
                self.fleet_handle = self.adapter.add_easy_fleet(self.fleet_config)
                self.fleet_handles[self.fleet_name] = self.fleet_handle
                self.configured_fleets = {self.fleet_name}
                self._setup_fleet_consideration(self.fleet_handle, self.fleet_name)

                all_wps, charger_wps, parking_wps, wp_coords, lane_segments, vertex_coords = self._parse_nav_waypoints(self.nav_graph_path)
                self.fleet_waypoint_info[self.fleet_name] = {
                    "all": all_wps,
                    "chargers": charger_wps,
                    "parkings": parking_wps,
                    "coords": wp_coords,
                    "lane_segments": lane_segments,
                    "vertex_coords": vertex_coords,
                    "levels": self._last_parsed_levels,
                    "graph_idx": 0,
                    "graph_sha": nav_graph_fingerprint(self.nav_graph_path),
                }

            print(f"[INFO] Open-RMF EasyFullControl Adapter ready for fleets: {list(self.fleet_handles.keys())}")
            self._publish_fa_state()
        except Exception as e:
            print(f"[ERROR] Failed to start live Open-RMF Adapter: {e}")
            print("[WARN] Switching to Mock/Standalone mode.")
            self.mock_rmf = True

    def _setup_fleet_consideration(self, handle: Any, fleet_name: str):
        """Register task consideration callbacks on FleetUpdateHandle to block bidding if all robots in fleet are off-graph."""
        if not handle or not hasattr(handle, "more"):
            return
        try:
            fuh = handle.more()
            if not fuh:
                return

            def _check_fleet_availability(fn: str) -> bool:
                with self.lock:
                    fleet_robs = [r for r in self.robots.values() if r.fleet_name == fn]
                    if not fleet_robs:
                        return True
                    eligible = [
                        r for r in fleet_robs
                        if r.is_online and not r.is_estopped and not r.is_off_graph
                    ]
                    return len(eligible) > 0

            def _consider_patrol(req):
                import rmf_adapter.fleet_update_handle as _fuh
                c = _fuh.Confirmation()
                if not _check_fleet_availability(fleet_name):
                    c.set_errors([f"車隊 [{fleet_name}] 內所有車輛皆偏離路網 (>{OFF_GRAPH_THRESHOLD_M}m) 或無法運作，拒絕競標接單"])
                    return c
                c.accept()
                return c

            def _consider_delivery(req):
                import rmf_adapter.fleet_update_handle as _fuh
                c = _fuh.Confirmation()
                if not _check_fleet_availability(fleet_name):
                    c.set_errors([f"車隊 [{fleet_name}] 內所有車輛皆偏離路網 (>{OFF_GRAPH_THRESHOLD_M}m) 或無法運作，拒絕競標接單"])
                    return c
                c.accept()
                return c

            fuh.consider_patrol_requests(_consider_patrol)
            fuh.consider_delivery_requests(_consider_delivery, _consider_delivery)
            fuh.consider_composed_requests(_consider_patrol)
            print(f"[RMF] Registered off-graph bid guard for fleet [{fleet_name}] successfully.")
        except Exception as e:
            print(f"[RMF WARN] Could not register consideration callbacks for [{fleet_name}]: {e}")

    # ==========================================================================
    # MQTT Callbacks & Connection
    # ==========================================================================
    def _on_mqtt_connect(self, client, userdata, flags, rc):
        self.mqtt_connected = rc == 0
        self._publish_fa_state()
        if rc == 0:
            print(f"[MQTT] Connected successfully to Broker at {self.mqtt_host}:{self.mqtt_port}")
            # Wildcard subscription to support both self.fleet_name and all other AMR fleets
            client.subscribe(f"{self.topic_prefix}/+/robot/+/register", qos=1)
            client.subscribe(f"{self.topic_prefix}/+/robot/+/heartbeat", qos=0)
            client.subscribe(f"{self.topic_prefix}/+/robot/+/deregister", qos=1)
            client.subscribe(f"{self.topic_prefix}/+/robot/+/status", qos=1)
            client.subscribe(f"{self.topic_prefix}/+/robot/+/command_result", qos=1)
            client.subscribe(f"{self.topic_prefix}/+/robot/+/task_state", qos=1)
            client.subscribe(f"{self.topic_prefix}/+/control", qos=1)
            print(f"[MQTT] Subscribed to wildcard -> {self.topic_prefix}/+/robot/+/* and {self.topic_prefix}/+/control")
        else:
            print(f"[MQTT] Connection failed with result code: {rc}")

    def _on_mqtt_disconnect(self, client, userdata, rc):
        self.mqtt_connected = False
        self._publish_fa_state()
        if rc != 0:
            print(f"[MQTT] Unexpected disconnection (rc={rc}). Reconnecting...")

    def _on_mqtt_message(self, client, userdata, msg):
        topic = msg.topic
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception as e:
            print(f"[MQTT ERROR] Invalid JSON received on topic {topic}: {e}")
            return

        parts = topic.split("/")
        # Fleet-level control action: rmf/{fleet_name}/control
        if len(parts) == 3 and parts[2] == "control":
            fleet_name = parts[1]
            self._handle_fleet_control(fleet_name, payload)
            return

        # Extract fleet_name & robot_id from topic: rmf/{fleet_name}/robot/{robot_id}/{action}
        if len(parts) >= 5 and parts[2] == "robot":
            fleet_name = parts[1]
            robot_id = parts[3]
            action = parts[4]
        else:
            fleet_name = payload.get("fleet_name", self.fleet_name)
            robot_id = payload.get("robot_id") or payload.get("id", "unknown")
            action = parts[-1]

        # Dispatch to respective handlers
        if action == "register":
            self._handle_register(robot_id, fleet_name, payload)
        elif action == "heartbeat":
            self._handle_heartbeat(robot_id, fleet_name, payload)
        elif action == "deregister":
            self._handle_deregister(robot_id, fleet_name, payload)
        elif action == "status":
            self._handle_status(robot_id, fleet_name, payload)
        elif action == "command_result":
            self._handle_command_result(robot_id, fleet_name, payload)
        elif action == "task_state":
            self._handle_task_state(robot_id, fleet_name, payload)

    def _handle_fleet_control(self, fleet_name: str, payload: Dict[str, Any]):
        action = payload.get("action")
        print(f"[FLEET CONTROL] Received control action '{action}' for fleet [{fleet_name}]")
        with self.lock:
            if action == "pause":
                self.paused_fleets.add(fleet_name)
                for (f_name, r_id), robot in self.robots.items():
                    if f_name == fleet_name:
                        robot.is_paused = True
                        self._sync_rmf_availability(robot)
                print(f"[FLEET CONTROL] Fleet [{fleet_name}] paused in Fleet Adapter.")
            elif action == "resume":
                self.paused_fleets.discard(fleet_name)
                for (f_name, r_id), robot in self.robots.items():
                    if f_name == fleet_name:
                        robot.is_paused = False
                        self._sync_rmf_availability(robot)
                        self._resume_held_release_locked(robot)
                print(f"[FLEET CONTROL] Fleet [{fleet_name}] resumed in Fleet Adapter.")

        self._persist_set("saved_maps/paused_fleets.json", self.paused_fleets)
        self._publish_fa_state()

    @staticmethod
    def _persist_set(subpath: str, values: Set[str]):
        """Atomically persist FA-owned state (the FA is the sole writer of these files)."""
        try:
            path = find_backend_path(subpath)
            tmp_path = f"{path}.{os.getpid()}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(sorted(values), f)
            os.replace(tmp_path, path)
        except Exception as e:
            print(f"[WARN] Failed to write {subpath}: {e}")

    def _handle_robot_control(self, fleet_name: str, robot_id: str, action: str):
        """Operator emergency stop / release for a single robot (from backend via /rmf/robot_control)."""
        key = f"{fleet_name}/{robot_id}"
        with self.lock:
            robot = self._get_robot(fleet_name, robot_id)
            if action == "estop":
                self.estopped_robots.add(key)
                if robot:
                    self._apply_estop(robot)
                else:
                    print(f"[ROBOT CONTROL WARN] E-stop recorded for unknown robot '{key}'; applied when it registers.")
            elif action == "release":
                self.estopped_robots.discard(key)
                if robot and robot.is_estopped:
                    robot.is_estopped = False
                    self._sync_rmf_availability(robot)
                    print(f"[ROBOT CONTROL] E-stop released for '{key}'.")
                    # Resume the RMF command that was interrupted/held by the e-stop (if its task is still active).
                    self._dispatch_deferred_command(robot)
                    self._resume_held_release_locked(robot)
            else:
                print(f"[ROBOT CONTROL WARN] Unknown action '{action}' for '{key}'. Ignoring.")
                return
        self._persist_set("saved_maps/estopped_robots.json", self.estopped_robots)
        self._publish_fa_state()

    def _apply_estop(self, robot: ManagedRobot):
        """Halt the robot now; keep the RMF execution and hold its command for re-dispatch on release."""
        robot.is_estopped = True
        task = robot.active_task
        owned_task = False
        if task:
            self._cancel_proceed_timer(task)
            if task.get("execution") is not None and task["execution"] is robot.current_execution:
                owned_task = True
                # Resume the RMF-owned task from the interrupted step on release, not from step 0.
                index = task["step_offset"] + task["current_step"] + (1 if task["step_status"] == "waiting" else 0)
                remaining = task["all_steps"][index:]
                if remaining:
                    task["estopped"] = True
                    robot.deferred_cmd_payload = self._task_payload(
                        robot.name, None, remaining, step_offset=index, all_steps=task["all_steps"],
                        auto_proceed=task["auto_proceed"], step_delay=task["step_delay"],
                        task_id=task["task_id"])
        if robot.current_execution and not owned_task and robot.deferred_cmd_payload is None and robot.last_cmd_payload:
            robot.deferred_cmd_payload = dict(robot.last_cmd_payload, cmd_id=None)
        robot.is_retrying = False
        robot.nav_retries = 0
        robot.target_position = None  # heartbeat must not complete the interrupted command
        robot.last_cmd_payload = None  # nor resend it as "lost"
        robot.cmd_id += 1  # late results of the interrupted command must not match
        robot.stop_cmd_id = robot.cmd_id
        cmd_topic = f"{self.topic_prefix}/{robot.fleet_name}/robot/{robot.name}/command"
        self._publish_command(robot, {
            "robot_id": robot.name,
            "cmd_id": robot.cmd_id,
            "action": "stop",
            "reason": "Emergency stop by operator",
            "timestamp": time.time(),
        })
        print(f"[ROBOT CONTROL] EMERGENCY STOP (cmd_id: {robot.cmd_id}) sent to '{robot.name}' via {cmd_topic}")
        if robot.update_handle:
            try:
                robot.update_handle.more().log_error(f"Robot '{robot.name}' emergency-stopped by operator.")
            except Exception as e:
                print(f"[RMF WARN] Error logging e-stop for '{robot.name}': {e}")
        self._sync_rmf_availability(robot)

    def _on_ros2_robot_control(self, msg: String):
        try:
            payload = json.loads(msg.data)
            fleet_name = payload.get("fleet")
            robot_id = payload.get("robot")
            if fleet_name and robot_id:
                self._handle_robot_control(fleet_name, robot_id, payload.get("action"))
        except Exception as e:
            print(f"[FA ERROR] Failed to process ROS 2 robot control: {e}")

    def _on_ros2_task_command(self, msg: String):
        """Processes task commands from backend/operator via /rmf/task_command."""
        try:
            payload = json.loads(msg.data)
            action = payload.get("action", "dispatch_task")
            fleet_name = payload.get("fleet_name") or payload.get("fleet")
            robot_id = payload.get("robot_id") or payload.get("robot")
            if not robot_id:
                print(f"[TASK COMMAND WARN] Missing 'robot_id' in task command: {payload}")
                return
            if not fleet_name:
                fleet_name = self._find_robot_fleet(robot_id)
            if not fleet_name:
                print(f"[TASK COMMAND WARN] Cannot determine fleet for robot '{robot_id}'.")
                return

            if action in ("dispatch_task", "task"):
                steps = payload.get("steps", [])
                task_id = payload.get("task_id")
                # The adapter decides when the next step starts: default is to wait for an operator proceed.
                res = self.dispatch_multi_step_task(
                    fleet_name=fleet_name,
                    robot_id=robot_id,
                    steps=steps,
                    auto_proceed=bool(payload.get("auto_proceed", False)),
                    step_delay=float(payload.get("step_delay", 0.0) or 0.0),
                    task_id=task_id,
                )
                if res.get("status") == "error":
                    self._publish_task_status(
                        fleet_name, robot_id, None, "rejected", 0,
                        len(steps) if isinstance(steps, list) else 0, res.get("message", ""), task_id)
            elif action == "proceed":
                self._dispatch_proceed(fleet_name, robot_id, payload.get("cmd_id"), manual=True)
            elif action == "stop":
                self._dispatch_stop(fleet_name, robot_id, payload.get("task_id"))
            else:
                print(f"[TASK COMMAND WARN] Unknown action '{action}' in task command: {payload}")
        except Exception as e:
            print(f"[FA ERROR] Failed to process ROS 2 task command: {e}")

    def _publish_task_status(
        self,
        fleet_name: str,
        robot_id: str,
        cmd_id: Any,
        status: str,
        step: int,
        total_steps: int,
        message: str,
        task_id: Optional[str] = None,
    ):
        """Publishes multi-step task status to ROS 2 /rmf/task_status topic."""
        if not hasattr(self, "task_status_pub") or not self.task_status_pub or not rclpy.ok():
            return
        try:
            msg = String()
            msg.data = json.dumps({
                "fleet_name": fleet_name,
                "robot_id": robot_id,
                "cmd_id": cmd_id,
                "status": status,
                "step": step,
                "steps": total_steps,
                "message": message,
                "task_id": task_id,
                "timestamp": time.time(),
            })
            self.task_status_pub.publish(msg)
        except Exception as e:
            print(f"[FA WARN] Failed to publish task status: {e}")

    def _publish_fa_state(self):
        """Publish FA runtime state (latched); doubles as the ACK for fleet pause/resume."""
        if not self.fa_state_pub:
            return
        try:
            msg = String()
            msg.data = json.dumps({
                "mqtt_connected": self.mqtt_connected,
                "paused_fleets": sorted(self.paused_fleets),
                "estopped_robots": sorted(self.estopped_robots),
                "started_at": self.started_at,
                "robots": {
                    f"{r.fleet_name}/{r.name}": {
                        "online": r.is_online,
                        "status": r.status,
                        "last_heartbeat": r.last_heartbeat if r.heartbeat_received else 0,
                        "busy": (
                            r.session_reset_pending
                            or r.current_execution is not None
                            or r.active_task is not None
                            or r.deferred_cmd_payload is not None
                            or r.nav_suspended
                        ),
                        "session_id": r.session_id,
                        "session_reset_pending": r.session_reset_pending,
                        "session_reset_error": r.session_reset_error,
                        "session_cleanup_at": r.session_cleanup_at,
                    }
                    for r in list(self.robots.values())
                },
                "fleets": {
                    name: {
                        "graph_idx": info.get("graph_idx", 0),
                        "graph_sha": info.get("graph_sha"),
                        "waypoints": info.get("all", []),
                    }
                    for name, info in list(self.fleet_waypoint_info.items())
                    if not self.mock_rmf and getattr(self, "fleet_handles", {}).get(name)
                },
                "waypoint_warnings": [
                    {"key": key, "message": m}
                    for key, msgs in sorted(list(self.waypoint_warnings.items()))
                    for m in msgs
                ],
                "off_graph_robots": {
                    f"{r.fleet_name}/{r.name}": {
                        "fleet": r.fleet_name,
                        "robot_id": r.name,
                        "distance": r.off_graph_distance,
                    }
                    for r in list(self.robots.values())
                    if r.is_off_graph
                },
                "timestamp": time.time(),
            })
            self.fa_state_pub.publish(msg)
        except Exception as e:
            print(f"[FA WARN] Failed to publish fleet adapter state: {e}")

    def _on_ros2_fleet_control(self, msg: String):
        try:
            payload = json.loads(msg.data)
            fleet_name = payload.get("fleet")
            if fleet_name:
                self._handle_fleet_control(fleet_name, payload)
        except Exception as e:
            print(f"[FA ERROR] Failed to process ROS 2 fleet control: {e}")

    def _save_discovered_robots(self):
        try:
            disc_path = find_backend_path("saved_maps/discovered_robots.json")
            os.makedirs(os.path.dirname(disc_path), exist_ok=True)
            with open(disc_path + ".tmp", "w", encoding="utf-8") as f:
                json.dump(self.discovered_robots, f, indent=2, ensure_ascii=False)
            os.replace(disc_path + ".tmp", disc_path)
            if self.discovered_pub and rclpy.ok():
                msg = String()
                msg.data = json.dumps(self.discovered_robots)
                self.discovered_pub.publish(msg)
        except Exception as e:
            print(f"[FA WARN] Failed to save discovered robots: {e}")

    def _resolve_charger_waypoint(
        self, robot_id: str, fleet_name: str, payload: Dict[str, Any], rejected: Optional[List[str]] = None
    ) -> str:
        fleet_info = self.fleet_waypoint_info.get(fleet_name, {})
        valid_wps = set(fleet_info.get("all", []))
        chargers = fleet_info.get("chargers", [])

        # 1. Check if payload explicitly provides a valid charger
        req_charger = payload.get("default_charger")
        if req_charger and (not valid_wps or req_charger in valid_wps):
            return req_charger
        if req_charger and rejected is not None:
            rejected.append(req_charger)

        # 2. Check fleet_adapters.json
        adapters_file = find_backend_path("saved_maps/fleet_adapters.json")
        if os.path.exists(adapters_file):
            try:
                with open(adapters_file, "r", encoding="utf-8") as f:
                    adapters_data = json.load(f)
                for item in adapters_data:
                    if item.get("fleet_name") == fleet_name:
                        for r in item.get("robots", []):
                            if r.get("name") == robot_id and r.get("charger_waypoint"):
                                c = r.get("charger_waypoint")
                                if not valid_wps or c in valid_wps:
                                    return c
                                if rejected is not None:
                                    rejected.append(c)
                        if item.get("default_charger"):
                            c = item.get("default_charger")
                            if not valid_wps or c in valid_wps:
                                return c
                            if rejected is not None:
                                rejected.append(c)
            except Exception:
                pass

        # 3. Use charger marked in the nav graph vertices
        if chargers:
            return chargers[0]

        # 4. Use first waypoint in the nav graph
        if valid_wps:
            return fleet_info.get("all", [])[0]

        return "charger_1"

    def _resolve_parking_waypoint(
        self, robot_id: str, fleet_name: str, payload: Dict[str, Any], rejected: Optional[List[str]] = None
    ) -> str:
        fleet_info = self.fleet_waypoint_info.get(fleet_name, {})
        valid_wps = set(fleet_info.get("all", []))
        parkings = fleet_info.get("parkings", [])

        req_parking = payload.get("default_parking")
        if req_parking and (not valid_wps or req_parking in valid_wps):
            return req_parking
        if req_parking and rejected is not None:
            rejected.append(req_parking)

        adapters_file = find_backend_path("saved_maps/fleet_adapters.json")
        if os.path.exists(adapters_file):
            try:
                with open(adapters_file, "r", encoding="utf-8") as f:
                    adapters_data = json.load(f)
                for item in adapters_data:
                    if item.get("fleet_name") == fleet_name:
                        for r in item.get("robots", []):
                            if r.get("name") == robot_id and r.get("parking_waypoint"):
                                p = r.get("parking_waypoint")
                                if not valid_wps or p in valid_wps:
                                    return p
                                if rejected is not None:
                                    rejected.append(p)
                        if item.get("default_parking"):
                            p = item.get("default_parking")
                            if not valid_wps or p in valid_wps:
                                return p
                            if rejected is not None:
                                rejected.append(p)
            except Exception:
                pass

        if parkings:
            return parkings[0]
        if valid_wps:
            return fleet_info.get("all", [])[0]
        return "parking_1"

    def _record_waypoint_fallbacks(
        self,
        robot_id: str,
        fleet_name: str,
        rejected_chargers: List[str],
        charger: str,
        rejected_parkings: List[str],
        parking: str,
    ):
        graph_idx = self.fleet_waypoint_info.get(fleet_name, {}).get("graph_idx", 0)
        messages = []
        for label, rejected, used in (("充電點", rejected_chargers, charger), ("停車點", rejected_parkings, parking)):
            for wp in dict.fromkeys(rejected):
                messages.append(f"{robot_id}: {label} '{wp}' 不在 Graph {graph_idx}，已改用 '{used}'")
        key = f"{fleet_name}_{robot_id}"
        if messages:
            for m in messages:
                print(f"[REGISTER WARN] {m}")
            self.waypoint_warnings[key] = messages
        else:
            self.waypoint_warnings.pop(key, None)
        self._publish_fa_state()

    # ==========================================================================
    # Handler 1: Robot Registration (開機註冊)
    # ==========================================================================
    def _handle_register(self, robot_id: str, fleet_name: str, payload: Dict[str, Any]):
        print(f"[REGISTER] Received registration request for robot '{robot_id}' in fleet '{fleet_name}': {payload}")

        session_id = payload.get("session_id")
        session_key = f"{fleet_name}/{robot_id}"
        previous = self.robot_sessions.get(session_key, {})
        if session_id is not None and (
            not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 128
        ):
            self._send_register_ack(
                robot_id, False, "session_id must be a non-empty string of at most 128 characters",
                fleet_name=fleet_name,
            )
            return
        if (previous and not session_id) or (session_id and session_id in previous.get("retired_ids", [])):
            self._send_register_ack(
                robot_id, False, "Missing or retired session_id; registration rejected",
                fleet_name=fleet_name, session_id=session_id,
            )
            return
        loc = payload.get("initial_location", {})
        if session_id and (
            not isinstance(loc, dict)
            or not all(
                isinstance(loc.get(k), (int, float)) and not isinstance(loc[k], bool)
                and math.isfinite(loc[k]) for k in ("x", "y", "yaw")
            )
        ):
            self._send_register_ack(
                robot_id, False, "Session registration requires finite initial_location x, y, yaw",
                fleet_name=fleet_name, session_id=session_id,
            )
            return
        x = float(loc.get("x", 0.0))
        y = float(loc.get("y", 0.0))
        yaw = float(loc.get("yaw", 0.0))
        level_name = loc.get("level_name") or self._default_level(fleet_name)
        rejected_chargers: List[str] = []
        rejected_parkings: List[str] = []
        default_charger = self._resolve_charger_waypoint(robot_id, fleet_name, payload, rejected_chargers)
        default_parking = self._resolve_parking_waypoint(robot_id, fleet_name, payload, rejected_parkings)
        print(f"[REGISTER] Resolved charger for '{robot_id}' ({fleet_name}): {default_charger}, parking: {default_parking}")
        self._record_waypoint_fallbacks(
            robot_id, fleet_name, rejected_chargers, default_charger, rejected_parkings, default_parking
        )
        initial_pos = np.array([x, y, yaw], dtype=np.float64)

        # Track in discovered_robots dictionary
        robot_key = f"{fleet_name}_{robot_id}"
        now = time.time()
        disc = self.discovered_robots.get(robot_key, {
            "robot_id": robot_id,
            "fleet_name": fleet_name,
            "x": x,
            "y": y,
            "yaw": yaw,
            "battery": 100.0,
            "status": "idle",
            "specs": payload.get("specs", {}),
            "initial_location": loc,
            "default_parking": default_parking,
            "default_charger": default_charger,
            "first_seen": now,
        })
        dist_to_graph = self._compute_distance_to_graph(fleet_name, x, y, level_name)
        is_off_graph = dist_to_graph > OFF_GRAPH_THRESHOLD_M
        disc["last_seen"] = now
        disc["x"] = x
        disc["y"] = y
        disc["yaw"] = yaw
        disc["is_off_graph"] = is_off_graph
        disc["off_graph_distance"] = round(dist_to_graph, 2)
        if "specs" in payload:
            disc["specs"] = payload["specs"]
        disc["status"] = "idle"
        disc["is_adopted"] = fleet_name in self.configured_fleets
        self.discovered_robots[robot_key] = disc
        self._save_discovered_robots()

        # 1. Reject unconfigured fleets explicitly (no silent fallback!)
        if fleet_name not in self.configured_fleets:
            print(f"[REGISTER WARN] Cannot register '{robot_id}': Fleet [{fleet_name}] is not configured in Open-RMF! Configured fleets: {list(self.configured_fleets)}")
            self._send_register_ack(
                robot_id,
                success=False,
                msg=f"Fleet [{fleet_name}] pending adoption in Web Studio",
                status="pending_adoption",
                fleet_name=fleet_name,
            )
            return

        with self.lock:
            # Check if robot already exists with a valid handle in the SAME fleet (reuse existing handle on re-register)
            robot_key = (fleet_name, robot_id)
            existing_robot = self.robots.get(robot_key)
            same_session = bool(session_id and existing_robot and existing_robot.session_id == session_id)
            needs_reset = bool(
                session_id and (not same_session or existing_robot.session_reset_error)
            )
            reset_tasks = bool(
                existing_robot or (previous and previous.get("session_id") != session_id)
                or previous.get("reset_tasks", False) and previous.get("reset_pending", False)
            )
            if needs_reset:
                retired = list(previous.get("retired_ids", []))
                if previous.get("session_id") and previous["session_id"] != session_id:
                    retired.append(previous["session_id"])
                self.robot_sessions[session_key] = {
                    "session_id": session_id, "retired_ids": retired,
                    "reset_pending": True, "reset_tasks": reset_tasks,
                    "cleanup_at": previous.get("cleanup_at", 0),
                    "started_at": now,
                }
                try:
                    self._save_robot_sessions()
                except OSError as e:
                    if previous:
                        self.robot_sessions[session_key] = previous
                    else:
                        self.robot_sessions.pop(session_key, None)
                    self._send_register_ack(
                        robot_id, False, f"Cannot persist session: {e}",
                        fleet_name=fleet_name, session_id=session_id,
                    )
                    print(f"[SESSION ERROR] Registration rejected: {e}")
                    return
            elif not session_id:
                print(f"[SESSION WARN] '{session_key}' uses legacy registration without session_id; "
                      "new-session detection is unavailable")

            if existing_robot and (self.mock_rmf or existing_robot.update_handle is not None):
                print(f"[REGISTER] Robot '{robot_id}' ({fleet_name}) re-registered. Reusing existing update_handle and updating position to ({x:.3f}, {y:.3f}, {yaw:.3f}).")
                existing_robot.is_online = True
                existing_robot.position = initial_pos
                existing_robot.map_name = level_name
                existing_robot.fleet_name = fleet_name
                existing_robot.is_off_graph = is_off_graph
                existing_robot.off_graph_distance = round(dist_to_graph, 2)
                if default_charger:
                    existing_robot.charger_waypoint = default_charger
                existing_robot.last_heartbeat = time.time()
                existing_robot.heartbeat_received = False
                existing_robot.is_paused = fleet_name in self.paused_fleets
                existing_robot.is_estopped = f"{fleet_name}/{robot_id}" in self.estopped_robots
                if needs_reset:
                    existing_robot.session_id = session_id
                    self._start_session_reset(existing_robot, reset_tasks, now)
                self._sync_rmf_availability(existing_robot)

                # Fresh registration signifies a new session/boot: abort any stale execution from prior session.
                # A deferred command was never sent to the robot, so it is not stale: dispatch it below.
                if not session_id and existing_robot.current_execution and existing_robot.deferred_cmd_payload is None:
                    print(f"[REGISTER WARN] Aborting stale execution for '{robot_id}' from prior session (cmd_id: {existing_robot.cmd_id}).")
                    existing_robot.current_execution = None
                    existing_robot.target_position = None
                    existing_robot.last_cmd_payload = None
                    existing_robot.nav_retries = 0
                    existing_robot.is_retrying = False
                    existing_robot.stop_cmd_id = None

                if not same_session:
                    existing_robot.status = "idle"

                if not self.mock_rmf and existing_robot.update_handle:
                    try:
                        rmf_state = rmf_easy.RobotState(level_name, initial_pos, existing_robot.battery_soc)
                        activity = existing_robot.current_execution.identifier if existing_robot.current_execution else None
                        existing_robot.update_handle.update(rmf_state, activity)
                    except Exception as e:
                        print(f"[WARN] Failed to update RMF state on re-register for '{robot_id}': {e}")

                self._send_register_ack(robot_id, success=True, msg="Registered successfully", fleet_name=fleet_name)
                # After the ACK, because the robot ignores commands until it is registered.
                self._dispatch_deferred_command(existing_robot)
                return

            # Live Open-RMF Registration
            update_handle = None
            target_handle = self.fleet_handles.get(fleet_name)
            if not self.mock_rmf:
                if not target_handle:
                    print(f"[ERROR] Cannot register '{robot_id}': No fleet handle found for fleet [{fleet_name}]!")
                    self._send_register_ack(robot_id, success=False, msg=f"Fleet [{fleet_name}] handle not found", fleet_name=fleet_name)
                    return

                try:
                    initial_state = rmf_easy.RobotState(level_name, initial_pos, 1.0)
                    robot_config = rmf_easy.RobotConfiguration([default_charger])

                    callbacks = rmf_easy.RobotCallbacks(
                        lambda dest, execution, fid=fleet_name, rid=robot_id: self._on_rmf_navigate(fid, rid, dest, execution),
                        lambda act_id, fid=fleet_name, rid=robot_id: self._on_rmf_stop(fid, rid, act_id),
                        lambda cat, desc, exec_h, fid=fleet_name, rid=robot_id: self._on_rmf_action(fid, rid, cat, desc, exec_h),
                    )
                    # Called when lift doors open on the destination floor: switch the robot's map over MQTT.
                    callbacks.localize = (
                        lambda dest, execution, fid=fleet_name, rid=robot_id: self._on_rmf_localize(fid, rid, dest, execution)
                    )

                    update_handle = target_handle.add_robot(
                        robot_id, initial_state, robot_config, callbacks
                    )
                except Exception as e:
                    print(f"[ERROR] Failed to add robot '{robot_id}' to RMF core: {e}")
                    self._send_register_ack(robot_id, success=False, msg=str(e), fleet_name=fleet_name)
                    return

                # 若直接 add_robot 回傳 None（例如車輛開機位置距離路網超過 RMF 初始吸附上限 OFF_GRAPH_THRESHOLD_M），
                # 嘗試使用該車隊路網最近站點作為初始錨點完成 Open-RMF 註冊，並立即以真實座標更新
                if update_handle is None:
                    f_info = self.fleet_waypoint_info.get(fleet_name, {})
                    level_info = (f_info.get("levels") or {}).get(level_name)
                    if level_info:
                        coords_dict = {
                            wp: rmf_to_robot(level_info["transform"], *xy)
                            for wp, xy in level_info["waypoints"].items()
                        }
                    else:
                        coords_dict = f_info.get("coords", {})
                    if coords_dict:
                        nearest_wp_name, (n_x, n_y) = min(
                            coords_dict.items(),
                            key=lambda item: (item[1][0] - x) ** 2 + (item[1][1] - y) ** 2,
                        )
                        dist = ((n_x - x) ** 2 + (n_y - y) ** 2) ** 0.5
                        print(
                            f"[REGISTER WARN] 車輛 '{robot_id}' 初始座標 ({x:.2f}, {y:.2f}) 距路網最近點 '{nearest_wp_name}' {dist:.2f}m (超出 RMF 吸附上限)。嘗試以 '{nearest_wp_name}' ({n_x:.2f}, {n_y:.2f}) 作為初始錨點註冊..."
                        )
                        snapped_state = rmf_easy.RobotState(
                            level_name, np.array([n_x, n_y, yaw], dtype=np.float64), 1.0
                        )
                        try:
                            update_handle = target_handle.add_robot(
                                robot_id, snapped_state, robot_config, callbacks
                            )
                            if update_handle is not None:
                                update_handle.update(initial_state, None)
                                print(
                                    f"[REGISTER SUCCESS] ✅ 車輛 '{robot_id}' 透過錨點 '{nearest_wp_name}' 成功完成 Open-RMF 納管並同步真實座標 ({x:.2f}, {y:.2f})！"
                                )
                        except Exception as snap_err:
                            print(f"[REGISTER ERROR] Snapped registration failed for '{robot_id}': {snap_err}")

                # add_robot() 與錨點 fallback 皆失敗時回傳錯誤
                if update_handle is None:
                    print(f"[ERROR] target_handle.add_robot() returned None for '{robot_id}' in fleet [{fleet_name}]!")
                    self._send_register_ack(
                        robot_id,
                        success=False,
                        msg="Failed to add robot to Open-RMF: add_robot returned None",
                        fleet_name=fleet_name,
                    )
                    return

                print(f"[RMF] Successfully registered '{robot_id}' into Open-RMF fleet [{fleet_name}] handle!")

            # Store in Managed Robots dictionary keyed by (fleet_name, robot_id)
            managed_robot = ManagedRobot(
                name=robot_id,
                fleet_name=fleet_name,
                map_name=level_name,
                position=initial_pos,
                battery_soc=1.0,
                update_handle=update_handle,
                charger_waypoint=default_charger,
            )
            managed_robot.is_off_graph = is_off_graph
            managed_robot.session_id = session_id
            managed_robot.session_cleanup_at = previous.get("cleanup_at", 0)
            managed_robot.off_graph_distance = round(dist_to_graph, 2)
            if is_off_graph:
                print(f"[OFF-GRAPH WARN] ⚠️ 車輛 '{robot_id}' 距離路網 {dist_to_graph:.2f}m > {OFF_GRAPH_THRESHOLD_M}m，標記為偏離路網，禁止競標與操控！")
            self.robots[robot_key] = managed_robot

            # Restore fleet pause / persisted operator e-stop for a newly added robot
            managed_robot.is_paused = fleet_name in self.paused_fleets
            managed_robot.is_estopped = f"{fleet_name}/{robot_id}" in self.estopped_robots
            if needs_reset:
                self._start_session_reset(managed_robot, reset_tasks, now)
            elif not self.mock_rmf and update_handle:
                threading.Thread(
                    target=self._enable_legacy_responsive_wait, args=(managed_robot,), daemon=True
                ).start()
            if managed_robot.is_paused or managed_robot.is_estopped:
                self._sync_rmf_availability(managed_robot)

        # Send ACK back to AMR
        self._send_register_ack(robot_id, success=True, msg="Registered successfully", fleet_name=fleet_name)

    def _send_register_ack(self, robot_id: str, success: bool, msg: str, status: Optional[str] = None,
                           fleet_name: Optional[str] = None, session_id: Optional[str] = None):
        target_fleet = fleet_name or self.fleet_name
        ack_topic = f"{self.topic_prefix}/{target_fleet}/robot/{robot_id}/register_ack"
        final_status = status if status else ("success" if success else "error")
        ack_payload = {
            "robot_id": robot_id,
            "status": final_status,
            "message": msg,
            "assigned_fleet": target_fleet,
            "server_time": time.time(),
        }
        session_id = session_id or self.robot_sessions.get(f"{target_fleet}/{robot_id}", {}).get("session_id")
        if session_id:
            ack_payload["session_id"] = session_id
        self.mqtt_client.publish(ack_topic, json.dumps(ack_payload), qos=1)
        print(f"[REGISTER] Sent ACK to '{robot_id}' on {ack_topic}: {ack_payload}")

    # ==========================================================================
    # Handler 2: Periodic Heartbeat (週期性遙測心跳)
    # ==========================================================================
    def _handle_heartbeat(self, robot_id: str, fleet_name: str, payload: Dict[str, Any]):
        robot_id = payload.get("robot_id") or robot_id
        if not self._session_matches(robot_id, fleet_name, payload):
            self._send_register_ack(
                robot_id, False, "Session not registered; please register", status="require_register",
                fleet_name=fleet_name, session_id=payload.get("session_id"),
            )
            return
        robot_entry = self._get_robot(fleet_name, robot_id)
        if robot_entry and robot_entry.session_id and not all(
            isinstance(payload.get(k), (int, float)) and not isinstance(payload[k], bool)
            and math.isfinite(payload[k]) for k in ("x", "y", "yaw")
        ):
            robot_entry.session_ready.clear()
            print(f"[SESSION WARN] Invalid localized heartbeat from '{fleet_name}/{robot_id}'")
            return

        # Update discovered_robots record
        robot_key = f"{fleet_name}_{robot_id}"
        now = time.time()
        x_val = float(payload.get("x", 0.0))
        y_val = float(payload.get("y", 0.0))
        yaw_val = float(payload.get("yaw", 0.0))
        bat_val = float(payload.get("battery", 100.0))
        status_val = payload.get("status", "idle")

        robot_entry = self.robots.get((fleet_name, robot_id))
        if robot_entry and getattr(robot_entry, "nav_suspended", False):
            x_val = float(robot_entry.position[0])
            y_val = float(robot_entry.position[1])
            yaw_val = float(robot_entry.position[2])
            hb_level = robot_entry.map_name
            dist_to_graph = 0.0
            is_off_graph = False
        else:
            hb_level = payload.get("level_name") or (robot_entry.map_name if robot_entry else self._default_level(fleet_name))
            dist_to_graph = self._compute_distance_to_graph(fleet_name, x_val, y_val, hb_level)
            is_off_graph = dist_to_graph > OFF_GRAPH_THRESHOLD_M

        with self.lock:
            if robot_key in self.discovered_robots:
                disc = self.discovered_robots[robot_key]
                if "x" in payload:
                    disc["x"] = x_val
                if "y" in payload:
                    disc["y"] = y_val
                if "yaw" in payload:
                    disc["yaw"] = yaw_val
                if "battery" in payload:
                    disc["battery"] = bat_val
                if "status" in payload:
                    disc["status"] = status_val
                disc["last_seen"] = now
                disc["is_adopted"] = fleet_name in self.configured_fleets
                disc["is_off_graph"] = is_off_graph
                disc["off_graph_distance"] = round(dist_to_graph, 2)
            else:
                self.discovered_robots[robot_key] = {
                    "robot_id": robot_id,
                    "fleet_name": fleet_name,
                    "x": x_val,
                    "y": y_val,
                    "yaw": yaw_val,
                    "battery": bat_val,
                    "status": status_val,
                    "first_seen": now,
                    "last_seen": now,
                    "is_adopted": fleet_name in self.configured_fleets,
                    "is_off_graph": is_off_graph,
                    "off_graph_distance": round(dist_to_graph, 2),
                }

            robot = self._get_robot(fleet_name, robot_id)
            if not robot or (not self.mock_rmf and robot.update_handle is None):
                # Robot sent heartbeat without registering first (e.g. FA late startup or restart)
                print(f"[WARN] Heartbeat from robot '{robot_id}' without active RMF handle ({fleet_name}). Requesting registration...")
                self._send_register_ack(
                    robot_id, success=False, msg="Robot not registered in RMF, please register",
                    status="require_register", fleet_name=fleet_name,
                    **({"session_id": payload["session_id"]} if payload.get("session_id") else {}),
                )
                return

            # Update telemetry (preserve frozen pose/map while navigation is suspended inside a lift)
            if getattr(robot, "nav_suspended", False):
                x = float(robot.position[0])
                y = float(robot.position[1])
                yaw = float(robot.position[2])
                level_name = robot.map_name
            else:
                x = float(payload.get("x", robot.position[0]))
                y = float(payload.get("y", robot.position[1]))
                yaw = float(payload.get("yaw", robot.position[2]))
                level_name = payload.get("level_name", robot.map_name)
            battery = float(payload.get("battery", robot.battery_soc * 100.0))
            status = payload.get("status", "idle")

            was_offline = not robot.is_online
            robot.position = np.array([x, y, yaw], dtype=np.float64)
            robot.battery_soc = max(0.0, min(1.0, battery / 100.0))
            robot.map_name = level_name
            robot.status = status
            robot.last_heartbeat = time.time()
            robot.heartbeat_received = True
            robot.is_online = True
            robot.is_off_graph = is_off_graph
            robot.off_graph_distance = round(dist_to_graph, 2)

            if was_offline:
                print(f"[HEARTBEAT] Robot '{robot_id}' came back online via heartbeat. Restoring RMF availability.")
                self._sync_rmf_availability(robot)
                self._dispatch_deferred_command(robot)

            # Resend active command if the robot's heartbeat indicates it was lost (e.g. after reboot)
            reported_cmd_id = payload.get("current_cmd_id")
            self._resend_if_lost(robot, reported_cmd_id)
            self._complete_by_heartbeat_locked(robot, reported_cmd_id)

            # Forward state update to Open-RMF core
            if not self.mock_rmf and robot.update_handle:
                try:
                    if robot.current_execution and not robot.current_execution.okay():
                        print(f"[NAV] Current execution for '{robot_id}' is no longer okay. Clearing execution.")
                        robot.current_execution = None
                        robot.target_position = None
                        robot.deferred_cmd_payload = None
                        robot.pending_suspend_lift = None

                    rmf_state = rmf_easy.RobotState(robot.map_name, robot.position, robot.battery_soc)
                    activity_id = (
                        robot.current_execution.identifier
                        if robot.current_execution and robot.current_execution.okay()
                        else None
                    )
                    robot.update_handle.update(rmf_state, activity_id)
                except Exception as e:
                    print(f"[ERROR] rmf update error for '{robot_id}': {e}")
                    return
            if robot.session_reset_pending:
                robot.session_ready.clear()
            if (
                robot.session_reset_pending and payload.get("localization_ready") is True
                and not is_off_graph and level_name in self.fleet_waypoint_info.get(fleet_name, {}).get("levels", {})
                and status in ("idle", "charging") and "current_cmd_id" in payload
                and payload["current_cmd_id"] is None
            ):
                robot.session_ready.set()

    # ==========================================================================
    # Handler 3: Deregister & Status (正常離線與異常斷線 LWT)
    # ==========================================================================
    def _handle_deregister(self, robot_id: str, fleet_name: str, payload: Dict[str, Any]):
        if not self._session_matches(robot_id, fleet_name, payload):
            return
        print(f"[DEREGISTER] Robot '{robot_id}' in '{fleet_name}' requested deregistration: {payload}")
        robot_key = f"{fleet_name}_{robot_id}"
        with self.lock:
            if robot_key in self.discovered_robots:
                self.discovered_robots[robot_key]["status"] = "offline"
                self.discovered_robots[robot_key]["last_seen"] = time.time()
                self._save_discovered_robots()

            robot = self._get_robot(fleet_name, robot_id)
            if robot:
                robot.is_online = False
                robot.session_ready.clear()
                robot.status = "offline"
                robot.pending_suspend_lift = None
                robot.nav_suspended = False
                robot.lift_entry_pose = None
                if robot.current_execution and robot.deferred_cmd_payload is None:
                    print(f"[DEREGISTER] Robot '{robot_id}' deregistered during active execution (cmd_id: {robot.cmd_id}). Aborting execution and requesting RMF replan.")
                    if robot.update_handle:
                        try:
                            robot.update_handle.more().log_error(f"Robot '{robot_id}' deregistered during active execution (cmd_id: {robot.cmd_id}).")
                        except Exception as e:
                            print(f"[RMF WARN] Error logging deregister abort for '{robot_id}': {e}")
                    robot.current_execution = None
                    robot.target_position = None
                    robot.last_cmd_payload = None
                    robot.nav_retries = 0
                    robot.is_retrying = False
                    robot.stop_cmd_id = None
                    robot.cmd_id += 1  # late results from the ended session must not match
                    self._sync_rmf_availability(robot)
                    # While offline, the re-issued command is deferred (see _defer_command), so no replan loop.
                    if robot.update_handle:
                        try:
                            robot.update_handle.more().replan()
                        except Exception as e:
                            print(f"[RMF WARN] Error requesting replan on deregister for '{robot_id}': {e}")
                else:
                    self._sync_rmf_availability(robot)
                print(f"[DEREGISTER] Robot '{robot_id}' marked offline (retaining update_handle, not deleted).")
            else:
                print(f"[DEREGISTER WARN] Cannot deregister '{robot_id}': robot not found in fleet '{fleet_name}'. Ignoring.")

    def _handle_status(self, robot_id: str, fleet_name: str, payload: Dict[str, Any]):
        if not self._session_matches(robot_id, fleet_name, payload):
            return
        status = payload.get("status", "unknown")
        print(f"[STATUS/LWT] Robot '{robot_id}' ({fleet_name}) reported status: {status}")
        robot_key = f"{fleet_name}_{robot_id}"
        with self.lock:
            if robot_key in self.discovered_robots:
                self.discovered_robots[robot_key]["status"] = status
                self.discovered_robots[robot_key]["last_seen"] = time.time()
                self._save_discovered_robots()

            if status in ["offline", "disconnected"]:
                robot = self._get_robot(fleet_name, robot_id)
                if robot:
                    if not robot.is_online and robot.status == "offline":
                        return
                    robot.is_online = False
                    robot.session_ready.clear()
                    robot.status = "offline"
                    self._sync_rmf_availability(robot)
                    print(f"[ALERT] Robot '{robot_id}' marked OFFLINE due to status message.")
                else:
                    print(f"[STATUS WARN] Cannot update status for '{robot_id}': robot not found in fleet '{fleet_name}'. Ignoring.")

    def _handle_command_result(self, robot_id: str, fleet_name: str, payload: Dict[str, Any]):
        if not self._session_matches(robot_id, fleet_name, payload):
            return
        cmd_id = payload.get("cmd_id")
        status = payload.get("status")
        print(f"[CMD_RESULT] Robot '{robot_id}' ({fleet_name}) reported cmd_id {cmd_id} status: {status}")

        with self.lock:
            robot = self._get_robot(fleet_name, robot_id)
            if not robot:
                print(f"[CMD_RESULT WARN] Received command_result for '{robot_id}' in fleet '{fleet_name}' but robot was not found. Ignoring.")
                return

            # Explicit check for STOP command confirmation (never complete previous navigation)
            if cmd_id is not None and cmd_id == robot.stop_cmd_id:
                print(f"[CMD_RESULT] Robot '{robot_id}' confirmed STOP execution (cmd_id: {cmd_id}, status: {status}).")
                robot.status = "idle"
                robot.stop_cmd_id = None
                return

            # Multi-step task result (task_state carries the same outcome; whichever arrives first wins)
            if robot.active_task and cmd_id == robot.active_task.get("cmd_id"):
                print(f"[CMD_RESULT] Robot '{robot_id}' ({fleet_name}) multi-step task (cmd_id: {cmd_id}) reported status: {status}")
                outcome = {"completed": "succeeded", "success": "succeeded",
                           "canceled": "canceled", "cancelled": "canceled"}.get(status, "aborted")
                self._finish_task_locked(robot, outcome, payload.get("message", ""))
                return

            if robot.current_execution and not robot.is_holding:
                if cmd_id == robot.cmd_id and status in ["completed", "success"]:
                    print(f"[NAV] Verified completion of cmd_id {cmd_id} for '{robot_id}'.")
                    action = (robot.last_cmd_payload or {}).get("action")
                    if action in ("navigate", "dock") and robot.pending_suspend_lift:
                        loc = payload.get("final_location")
                        if isinstance(loc, dict) and "x" in loc and "y" in loc:
                            robot.position = np.array(
                                [float(loc["x"]), float(loc["y"]), float(loc.get("yaw", robot.position[2]))],
                                dtype=np.float64,
                            )
                        elif robot.target_position is not None:
                            robot.position = np.array(robot.target_position, dtype=np.float64)
                        self._dispatch_prepare_floor_change_locked(
                            robot, robot.pending_suspend_lift, robot.current_execution, pose=robot.position
                        )
                        return
                    if action == "prepare_floor_change":
                        # Prefer the pose the robot reports at the moment it froze localization.
                        loc = payload.get("final_location")
                        pose = loc if isinstance(loc, dict) else ((robot.last_cmd_payload or {}).get("pose") or {})
                        if all(k in pose for k in ("x", "y", "yaw")):
                            robot.position = np.array(
                                [float(pose["x"]), float(pose["y"]), float(pose["yaw"])], dtype=np.float64
                            )
                        robot.lift_entry_pose = (robot.map_name, np.array(robot.position, dtype=np.float64))
                        robot.nav_suspended = True
                        robot.pending_suspend_lift = None
                        print(f"[LIFT] Robot '{robot.name}' prepared floor change (suspended navigation) inside lift '{(robot.last_cmd_payload or {}).get('lift_name')}' on floor '{robot.map_name}'.")
                    elif action == "change_floor":
                        robot.nav_suspended = False
                        robot.pending_suspend_lift = None
                        robot.lift_entry_pose = None
                        self._apply_floor_change_locked(robot, robot.last_cmd_payload)
                    try:
                        robot.current_execution.finished()
                    except Exception as e:
                        print(f"[NOTICE] Finish callback notice: {e}")
                    robot.current_execution = None
                    robot.target_position = None
                    robot.last_cmd_payload = None
                    robot.nav_retries = 0
                    robot.is_retrying = False
                elif cmd_id == robot.cmd_id and status in ["canceled", "cancelled"]:
                    print(f"[NAV] Robot '{robot_id}' reported command {cmd_id} was canceled.")
                    robot.status = "idle"
                    robot.current_execution = None
                    robot.target_position = None
                    robot.last_cmd_payload = None
                    robot.pending_suspend_lift = None
                    robot.nav_retries = 0
                    robot.is_retrying = False
                elif cmd_id == robot.cmd_id and status in ["failed", "aborted"]:
                    print(f"[NAV WARN] Robot '{robot_id}' reported failure for cmd_id {cmd_id} (status: {status}).")
                    if robot.is_retrying:
                        print(f"[NAV RETRY] Retry already in progress for '{robot_id}'. Ignoring duplicate failure report.")
                        return

                    # Only navigation is retried (obstacles may clear); gimbal/other action failures are
                    # deterministic (limits, no gimbal) or already timed out on the robot, so escalate at once.
                    failed_action = (robot.last_cmd_payload or {}).get("action") or "command"
                    retryable = failed_action in ("navigate", "dock")
                    if retryable and robot.nav_retries < 2:
                        robot.nav_retries += 1
                        robot.is_retrying = True
                        print(f"[NAV RETRY] Scheduling retry for cmd_id {cmd_id} on '{robot_id}' (attempt {robot.nav_retries}/2) in 1.2s...")
                        def retry_nav():
                            with self.lock:
                                robot.is_retrying = False
                                if robot.current_execution and robot.cmd_id == cmd_id and robot.last_cmd_payload:
                                    cmd_topic = f"{self.topic_prefix}/{robot.fleet_name}/robot/{robot_id}/command"
                                    payload_to_send = dict(robot.last_cmd_payload)
                                    payload_to_send["timestamp"] = time.time()
                                    robot.last_dispatch_time = time.time()
                                    self._publish_command(robot, payload_to_send)
                                    print(f"[NAV RETRY] Re-dispatched cmd_id {cmd_id} to '{robot_id}'")
                        threading.Timer(1.2, retry_nav).start()
                    else:
                        if retryable:
                            print(f"[NAV ERROR] Retries exhausted ({robot.nav_retries}/2) for cmd_id {cmd_id} on '{robot_id}'. Escalating failure to Open-RMF.")
                            error_msg = f"Navigation failed for cmd_id {cmd_id} after {robot.nav_retries} attempts."
                        else:
                            print(f"[ACTION ERROR] '{failed_action}' cmd_id {cmd_id} failed on '{robot_id}' (not retried). Escalating failure to Open-RMF.")
                            error_msg = f"Action '{failed_action}' failed for cmd_id {cmd_id}: {payload.get('message', '')}"
                        robot.status = "failed"
                        robot.is_retrying = False
                        robot.last_cmd_payload = None
                        if not self.mock_rmf and robot.update_handle:
                            try:
                                robot.update_handle.more().log_error(error_msg)
                                robot.update_handle.more().override_status("failed")
                                robot.update_handle.more().replan()
                            except Exception as e:
                                print(f"[RMF ERROR] Error escalating failure to RMF: {e}")

    # ==========================================================================
    # Handler 4: Multi-Step Task Progress & Gate Release (多步任務與步驟放行)
    #
    # Per multi_step_task.md the robot never advances on its own: after each step it
    # reports 'waiting' and the adapter alone decides when to send 'proceed'.
    # Methods suffixed with _locked must be called with self.lock held (it is not reentrant).
    # ==========================================================================
    TASK_TERMINAL_STATES = ("succeeded", "aborted", "rejected", "canceled")

    @staticmethod
    def _cancel_proceed_timer(task: Dict[str, Any]):
        timer = task.get("proceed_timer")
        if timer:
            try:
                timer.cancel()
            except Exception:
                pass
        task["proceed_timer"] = None

    @staticmethod
    def _task_payload(robot_id, cmd_id, steps, step_offset=0, all_steps=None,
                      auto_proceed=False, step_delay=0.0, task_id=None) -> Dict[str, Any]:
        """Deferred task command; the meta keys are adapter-internal and never sent over MQTT."""
        return {
            "robot_id": robot_id,
            "cmd_id": cmd_id,
            "action": "task",
            "steps": steps,
            "step_offset": step_offset,
            "all_steps": all_steps if all_steps is not None else steps,
            "auto_proceed": auto_proceed,
            "step_delay": step_delay,
            "task_id": task_id,
            "timestamp": time.time(),
        }

    def _escalate_rmf_failure(self, robot: ManagedRobot, message: str):
        """easy_full_control has no execution-failure API: report the error and mark the robot failed.
        The execution is kept so the RMF task stays visible until it is canceled/replanned in RMF."""
        print(f"[RMF ERROR] {message}")
        if robot.update_handle:
            try:
                robot.update_handle.more().log_error(message)
                robot.update_handle.more().override_status("failed")
            except Exception as e:
                print(f"[RMF WARN] Error escalating task failure for '{robot.name}': {e}")

    def _detach_active_task_locked(self, robot: ManagedRobot, reason: str):
        """Stop tracking the active task as current (a newer command replaces it). Its task_refs entry
        stays so the robot's final report for it still reaches the backend."""
        task = robot.active_task
        if not task:
            return
        self._cancel_proceed_timer(task)
        robot.active_task = None
        print(f"[TASK] Detached task '{task['task_id']}' (cmd_id: {task['cmd_id']}) from '{robot.name}': {reason}")

    def _handle_task_state(self, robot_id: str, fleet_name: str, payload: Dict[str, Any]):
        """Handles task progress published on rmf/<fleet>/robot/<robot>/task_state.

        - running: current step executing
        - waiting: step finished, robot holds at the release gate until 'proceed'
        - succeeded / aborted / rejected / canceled: terminal
        - canceling: stop received, waiting for the step to end
        """
        if not self._session_matches(robot_id, fleet_name, payload):
            return
        cmd_id = payload.get("cmd_id")
        status = payload.get("status")
        rel_step = int(payload.get("step", 0) or 0)
        message = payload.get("message", "")
        print(f"[TASK_STATE] Robot '{robot_id}' ({fleet_name}) cmd_id={cmd_id} status='{status}' step={rel_step}/{payload.get('steps', 0)} msg='{message}'")

        with self.lock:
            robot = self._get_robot(fleet_name, robot_id)
            if not robot:
                print(f"[TASK_STATE WARN] Robot '{robot_id}' ({fleet_name}) not found. Ignoring.")
                return

            task = robot.active_task
            if not task or cmd_id != task["cmd_id"]:
                ref = robot.task_refs.get(cmd_id)
                if ref is None:
                    print(f"[TASK_STATE] Ignoring state for unknown/stale cmd_id {cmd_id} on '{robot_id}'.")
                    return
                # Superseded task: only forward its progress / final state to the backend.
                if status in self.TASK_TERMINAL_STATES:
                    robot.task_refs.pop(cmd_id, None)
                self._publish_task_status(fleet_name, robot_id, cmd_id, status, ref["step_offset"] + rel_step,
                                          ref["total"], message, ref["task_id"])
                return

            if status in self.TASK_TERMINAL_STATES:
                task["current_step"] = rel_step
                self._finish_task_locked(robot, status, message)
                return

            task["current_step"] = rel_step
            task["step_status"] = status
            task["message"] = message
            task["last_update"] = time.time()
            step = task["step_offset"] + rel_step
            total = len(task["all_steps"])
            self._publish_task_status(fleet_name, robot_id, cmd_id, status, step, total, message, task["task_id"])

            if status == "running":
                robot.status = "moving"
                current = task["steps"][rel_step] if 0 <= rel_step < len(task["steps"]) else None
                if current and current.get("type") == "navigate":
                    robot.target_position = np.array(
                        [current["x"], current["y"], current.get("yaw", 0.0)], dtype=np.float64)
                else:
                    robot.target_position = None
            elif status == "waiting":
                robot.status = "waiting"
                robot.target_position = None
                print(f"[TASK_STATE] Robot '{robot_id}' finished step {step + 1}/{total}, holding at release gate.")
                if not task["auto_proceed"]:
                    print(f"[TASK_STATE] Awaiting operator proceed for '{robot_id}' (cmd_id: {cmd_id}).")
                elif task["step_delay"] > 0.0:
                    timer = threading.Timer(task["step_delay"], self._dispatch_proceed, args=[fleet_name, robot_id, cmd_id])
                    timer.daemon = True
                    task["proceed_timer"] = timer
                    timer.start()
                else:
                    self._send_proceed_locked(robot, cmd_id)
            elif status == "canceling":
                robot.status = "canceling"

    def _finish_task_locked(self, robot: ManagedRobot, outcome: str, message: str = ""):
        """Single, idempotent terminal handling for the active task (task_state and command_result
        both report the outcome; whichever arrives first finishes it)."""
        task = robot.active_task
        if task is None:
            return
        self._cancel_proceed_timer(task)
        robot.active_task = None
        robot.task_refs.pop(task["cmd_id"], None)
        execution = task.get("execution")
        owns_execution = execution is not None and execution is robot.current_execution
        step = task["step_offset"] + task.get("current_step", 0)
        total = len(task["all_steps"])
        robot.target_position = None
        robot.last_cmd_payload = None
        robot.nav_retries = 0
        robot.is_retrying = False

        if outcome == "canceled" and task.get("estopped") and owns_execution and robot.deferred_cmd_payload is not None:
            # Interrupted by e-stop: RMF still owns the task; remaining steps resume on release.
            robot.status = "idle"
            resume_at = robot.deferred_cmd_payload.get("step_offset", step)
            msg = f"E-stop interrupted; resumes from step {resume_at + 1}/{total} on release"
            print(f"[TASK HELD] '{robot.name}' task '{task['task_id']}': {msg}")
            self._publish_task_status(robot.fleet_name, robot.name, task["cmd_id"], "held", step, total, msg, task["task_id"])
            return

        if outcome == "succeeded":
            robot.status = "idle"
            print(f"[TASK_STATE SUCCESS] '{robot.name}' completed task '{task['task_id']}' ({total} steps).")
            if owns_execution:
                robot.deferred_cmd_payload = None
                try:
                    execution.finished()
                except Exception as ex:
                    print(f"[TASK NOTICE] execution finish notice: {ex}")
                robot.current_execution = None
        elif outcome == "canceled":
            robot.status = "idle"
            print(f"[TASK_STATE CANCELED] '{robot.name}' task '{task['task_id']}' canceled at step {step}.")
            if owns_execution:
                # RMF did not ask for this cancel (an RMF stop detaches the task first).
                robot.deferred_cmd_payload = None
                self._escalate_rmf_failure(robot, f"Task '{task['task_id']}' on '{robot.name}' was canceled by the robot at step {step}: {message}")
        else:
            robot.status = "failed"
            print(f"[TASK_STATE FAILED] '{robot.name}' task '{task['task_id']}' {outcome} at step {step}: {message}")
            if owns_execution:
                robot.deferred_cmd_payload = None
                self._escalate_rmf_failure(robot, f"Task '{task['task_id']}' on '{robot.name}' {outcome} at step {step}: {message}")

        self._publish_task_status(robot.fleet_name, robot.name, task["cmd_id"], outcome, step, total, message, task["task_id"])

    def _dispatch_multi_step_task_locked(
        self,
        robot: ManagedRobot,
        steps: Any,
        execution: Any = None,
        auto_proceed: bool = False,
        step_delay: float = 0.0,
        task_id: Optional[str] = None,
        step_offset: int = 0,
        all_steps: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        fleet_name, robot_id = robot.fleet_name, robot.name
        if robot.session_reset_pending:
            return {"status": "error", "message": "Vehicle session is awaiting cleanup and valid localization"}
        if not robot.is_online or robot.is_estopped:
            reason = "emergency-stopped" if robot.is_estopped else "offline"
            return {"status": "error", "message": f"Cannot execute task: robot '{robot_id}' in fleet '{fleet_name}' is {reason}."}

        ok, err_msg, validated_steps = validate_task_steps(steps)
        if not ok:
            return {"status": "error", "message": f"Steps validation failed: {err_msg}"}

        if execution is None and (robot.current_execution is not None or robot.deferred_cmd_payload is not None):
            # Direct (non-RMF) tasks must not pre-empt work that RMF has scheduled on this robot.
            return {"status": "error", "message": f"Robot '{robot_id}' is executing an Open-RMF task; direct task rejected."}

        if robot.is_off_graph:
            print(f"[TASK DISPATCH WARN] Robot '{robot_id}' is off-graph ({robot.off_graph_distance}m), executing direct multi-step task.")

        self._detach_active_task_locked(robot, "superseded by a new task")
        all_steps = list(all_steps) if all_steps is not None else validated_steps
        task_id = task_id or f"task_{robot.cmd_id + 1}"

        robot.cmd_id += 1
        cmd_id = robot.cmd_id
        cmd_payload = {
            "robot_id": robot_id,
            "cmd_id": cmd_id,
            "action": "task",
            "steps": validated_steps,
            "timestamp": time.time(),
        }
        robot.active_task = {
            "task_id": task_id,
            "cmd_id": cmd_id,
            "steps": validated_steps,
            "all_steps": all_steps,
            "step_offset": step_offset,
            "total_steps": len(all_steps),
            "current_step": 0,
            "step_status": "dispatched",
            "auto_proceed": bool(auto_proceed),
            "step_delay": max(0.0, float(step_delay)),
            "execution": execution,
            "proceed_timer": None,
            "release_pending": False,
            "estopped": False,
            "message": "",
            "started_at": time.time(),
            "last_update": time.time(),
        }
        robot.task_refs[cmd_id] = {"task_id": task_id, "step_offset": step_offset, "total": len(all_steps)}
        if execution is not None:
            robot.current_execution = execution
            robot.deferred_cmd_payload = None
        robot.nav_retries = 0
        robot.is_retrying = False
        robot.last_cmd_payload = cmd_payload
        robot.last_dispatch_time = time.time()
        robot.is_holding = False

        first = validated_steps[0]
        if first["type"] == "navigate":
            robot.target_position = np.array([first["x"], first["y"], first.get("yaw", 0.0)], dtype=np.float64)
        else:
            robot.target_position = None

        cmd_topic = f"{self.topic_prefix}/{fleet_name}/robot/{robot_id}/command"
        self._publish_command(robot, cmd_payload)
        print(f"[RMF -> AMR] Dispatched TASK '{task_id}' (cmd_id: {cmd_id}, steps: {step_offset + 1}-{step_offset + len(validated_steps)}/{len(all_steps)}, auto_proceed: {auto_proceed}) to '{robot_id}' via {cmd_topic}")
        self._publish_task_status(fleet_name, robot_id, cmd_id, "dispatched", step_offset, len(all_steps), "Task dispatched", task_id)
        return {
            "status": "success",
            "cmd_id": cmd_id,
            "robot_id": robot_id,
            "fleet_name": fleet_name,
            "task_id": task_id,
            "total_steps": len(all_steps),
        }

    def dispatch_multi_step_task(
        self,
        fleet_name: str,
        robot_id: str,
        steps: List[Dict[str, Any]],
        execution: Any = None,
        auto_proceed: bool = False,
        step_delay: float = 0.0,
        task_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Dispatches a multi-step task to the robot via MQTT 'action': 'task'.
        The robot halts at the release gate after every step until this adapter sends 'proceed'."""
        with self.lock:
            robot = self._get_robot(fleet_name, robot_id)
            if not robot:
                res = {"status": "error", "message": f"Robot '{robot_id}' in fleet '{fleet_name}' not found."}
            else:
                res = self._dispatch_multi_step_task_locked(
                    robot, steps, execution=execution, auto_proceed=auto_proceed,
                    step_delay=step_delay, task_id=task_id)
        if res["status"] == "error":
            print(f"[TASK DISPATCH ERROR] {res['message']}")
        return res

    def _send_proceed_locked(self, robot: ManagedRobot, cmd_id: Optional[int] = None) -> bool:
        """Release the gate of the active task. Only valid while the robot reports 'waiting'."""
        task = robot.active_task
        if not task:
            print(f"[PROCEED WARN] '{robot.name}' has no active multi-step task. Ignoring proceed.")
            return False
        if cmd_id is not None and cmd_id != task["cmd_id"]:
            print(f"[PROCEED WARN] cmd_id {cmd_id} does not match active task cmd_id {task['cmd_id']} on '{robot.name}'. Ignoring.")
            return False
        if task["step_status"] != "waiting":
            print(f"[PROCEED WARN] '{robot.name}' task is '{task['step_status']}', not waiting at a release gate. Ignoring.")
            return False
        task["proceed_timer"] = None
        if robot.is_paused or robot.is_estopped:
            task["release_pending"] = True
            print(f"[PROCEED HOLD] '{robot.name}' is paused or emergency-stopped; release held until resumed.")
            return False
        cmd_topic = f"{self.topic_prefix}/{robot.fleet_name}/robot/{robot.name}/command"
        self._publish_command(robot, {
            "robot_id": robot.name,
            "cmd_id": task["cmd_id"],
            "action": "proceed",
            "timestamp": time.time(),
        })
        task["step_status"] = "releasing"
        task["release_pending"] = False
        task["current_step"] = min(task.get("current_step", 0) + 1, len(task["all_steps"]) - 1)
        step = task["step_offset"] + task["current_step"]
        total = len(task["all_steps"])
        self._publish_task_status(robot.fleet_name, robot.name, task["cmd_id"], "running", step, total, "Proceeded to next step", task["task_id"])
        print(f"[RMF -> AMR] Dispatched PROCEED (cmd_id: {task['cmd_id']}) to '{robot.name}' via {cmd_topic}")
        return True

    def _resume_held_release_locked(self, robot: ManagedRobot):
        """After pause/e-stop is lifted, send a release that was held (auto mode or operator proceed)."""
        task = robot.active_task
        if (task and task["step_status"] == "waiting" and not robot.is_paused and not robot.is_estopped
                and (task["auto_proceed"] or task["release_pending"])):
            print(f"[TASK] Sending held release for '{robot.name}' (cmd_id: {task['cmd_id']}).")
            self._send_proceed_locked(robot, task["cmd_id"])

    def _dispatch_proceed(self, fleet_name: str, robot_id: str, cmd_id: Optional[int] = None, manual: bool = False):
        """Thread-safe proceed entry point (operator command or auto-proceed timer)."""
        with self.lock:
            robot = self._get_robot(fleet_name, robot_id)
            if not robot:
                print(f"[PROCEED WARN] Robot '{robot_id}' not found in fleet '{fleet_name}'.")
                return
            task = robot.active_task
            if not manual and (task is None or task["cmd_id"] != cmd_id):
                return  # stale auto-proceed timer
            self._send_proceed_locked(robot, cmd_id)

    def _send_stop_locked(self, robot: ManagedRobot, task_id: Optional[str] = None):
        task = robot.active_task
        if task_id is not None and (task is None or task["task_id"] != task_id):
            # The task is no longer running on the robot (finished, superseded or never started).
            stale = [c for c, ref in robot.task_refs.items() if ref["task_id"] == task_id]
            for c in stale:
                robot.task_refs.pop(c, None)
            print(f"[TASK STOP] Task '{task_id}' is not active on '{robot.name}'; reporting canceled without stopping the robot.")
            self._publish_task_status(robot.fleet_name, robot.name, stale[0] if stale else None,
                                      "canceled", 0, 0, "Task not active on robot", task_id)
            return
        if task:
            self._cancel_proceed_timer(task)
            if task.get("execution") is not None:
                print(f"[TASK STOP WARN] Canceling RMF-owned task '{task['task_id']}' on '{robot.name}' from the backend; RMF will be told it failed.")
        robot.cmd_id += 1
        robot.stop_cmd_id = robot.cmd_id
        cmd_topic = f"{self.topic_prefix}/{robot.fleet_name}/robot/{robot.name}/command"
        self._publish_command(robot, {
            "robot_id": robot.name,
            "cmd_id": robot.cmd_id,
            "action": "stop",
            "reason": "Operator / Scheduler STOP command",
            "timestamp": time.time(),
        })
        print(f"[RMF -> AMR] Dispatched STOP (cmd_id: {robot.cmd_id}) to '{robot.name}' via {cmd_topic}")
        if task:
            self._publish_task_status(robot.fleet_name, robot.name, task["cmd_id"], "canceling",
                                      task["step_offset"] + task.get("current_step", 0), len(task["all_steps"]),
                                      "Stop sent, waiting for robot confirmation", task["task_id"])

    def _dispatch_stop(self, fleet_name: str, robot_id: str, task_id: Optional[str] = None):
        """Thread-safe stop entry point; with task_id only that task is stopped."""
        with self.lock:
            robot = self._get_robot(fleet_name, robot_id)
            if not robot:
                print(f"[TASK STOP WARN] Robot '{robot_id}' not found in fleet '{fleet_name}'.")
                return
            self._send_stop_locked(robot, task_id)

    def _find_robot_fleet(self, robot_id: str) -> Optional[str]:
        """Finds the fleet name for a robot ID."""
        for (f_name, r_id) in self.robots.keys():
            if r_id == robot_id:
                return f_name
        for disc in self.discovered_robots.values():
            if disc.get("robot_id") == robot_id:
                return disc.get("fleet_name")
        return self.fleet_name

    # ==========================================================================
    # RMF -> Robot Outbound Callbacks (調度下發指令)
    # ==========================================================================
    @staticmethod
    def _navigate_payload(robot_id, cmd_id, target_pos, target_map, dock_name, speed_limit) -> Dict[str, Any]:
        return {
            "robot_id": robot_id,
            "cmd_id": cmd_id,
            "action": "dock" if dock_name else "navigate",
            "target": {
                "x": float(target_pos[0]),
                "y": float(target_pos[1]),
                "yaw": float(target_pos[2]),
                "level_name": target_map,
            },
            "dock_name": dock_name,
            "speed_limit": speed_limit,
            "timestamp": time.time(),
        }

    def _is_precise_waypoint(self, fleet_name: str, target_map: str, destination: Any) -> bool:
        """Whether this navigate must wait for the robot's command_result (see ARRIVAL_DISTANCE_M)."""
        f_info = (getattr(self, "fleet_waypoint_info", None) or {}).get(fleet_name) or {}
        lvl_info = (f_info.get("levels") or {}).get(target_map) or {}
        wp_name = str(getattr(destination, "name", "") or "")
        return bool(wp_name) and wp_name in (lvl_info.get("precise_waypoints") or ())

    def _complete_by_heartbeat_locked(self, robot: ManagedRobot, reported_cmd_id: Any):
        """Finish a navigate to an ordinary waypoint once the robot is near enough to it; the robot
        keeps its Nav2 goal until RMF's next navigate preempts it."""
        payload = robot.last_cmd_payload or {}
        if (
            robot.current_execution is None
            or robot.precise_arrival
            or robot.is_holding
            or robot.nav_suspended
            or robot.pending_suspend_lift
            or robot.active_task is not None
            or robot.deferred_cmd_payload is not None
            or robot.target_position is None
            or payload.get("action") != "navigate"
            or payload.get("cmd_id") != robot.cmd_id
            or (reported_cmd_id is not None and reported_cmd_id != robot.cmd_id)
            or (payload.get("target") or {}).get("level_name", robot.map_name) != robot.map_name
        ):
            return
        dist = math.hypot(
            robot.target_position[0] - robot.position[0], robot.target_position[1] - robot.position[1])
        yaw_diff = abs(math.remainder(float(robot.target_position[2] - robot.position[2]), 2 * math.pi))
        if dist >= ARRIVAL_DISTANCE_M or yaw_diff >= ARRIVAL_YAW_RAD:
            return
        print(f"[NAV] Robot '{robot.name}' reached ordinary waypoint for cmd_id {robot.cmd_id} via heartbeat "
              f"(dist: {dist:.3f}m, yaw_diff: {yaw_diff:.3f}rad). Finishing execution.")
        try:
            robot.current_execution.finished()
        except Exception as e:
            print(f"[NAV NOTICE] Execution finish exception: {e}")
        robot.current_execution = None
        robot.target_position = None
        robot.last_cmd_payload = None
        robot.nav_retries = 0
        robot.is_retrying = False

    def _resolve_destination_lift(
        self, fleet_name: str, destination: Any, target_map: str, target_pos: Any
    ) -> Optional[str]:
        """Return the lift name if ``destination`` is a lift cabin waypoint, otherwise None."""
        inside = getattr(destination, "inside_lift", None)
        if isinstance(inside, str) and inside:
            return inside
        if inside is not None and getattr(inside, "name", None):
            return str(inside.name)

        f_info = (getattr(self, "fleet_waypoint_info", None) or {}).get(fleet_name) or {}
        lvl_info = (f_info.get("levels") or {}).get(target_map) or {}
        wp_name = str(getattr(destination, "name", "") or "")
        lift_wps = lvl_info.get("lift_waypoints") or {}
        if wp_name and wp_name in lift_wps:
            return lift_wps[wp_name]

        transform = lvl_info.get("transform")
        for lift_name, vx, vy in lvl_info.get("lift_cabins") or []:
            rx, ry = rmf_to_robot(transform, vx, vy)
            if math.hypot(float(target_pos[0]) - rx, float(target_pos[1]) - ry) < 0.25:
                return str(lift_name)
        return None

    def _dispatch_prepare_floor_change_locked(
        self, robot: ManagedRobot, lift_name: str, execution: Any, pose: Any = None
    ):
        """Step 2 of multi-floor travel: after reaching the lift cabin, instruct the robot via MQTT
        (action: 'prepare_floor_change') to suspend navigation/localization before completing the RMF
        boarding execution (so the lift only closes doors and moves after the robot confirms command_result)."""
        cabin_pose = pose if pose is not None else robot.position
        robot.pending_suspend_lift = None
        if not robot.is_online or robot.is_estopped:
            self._defer_command(
                robot,
                execution,
                prepare_floor_change_payload(robot.name, 0, lift_name, robot.map_name, cabin_pose),
                f"Cannot prepare floor change in lift '{lift_name}': robot '{robot.name}' in fleet '{robot.fleet_name}' is "
                f"{'emergency-stopped' if robot.is_estopped else 'offline'}.",
            )
            return
        robot.is_holding = False
        robot.cmd_id += 1
        robot.nav_retries = 0
        robot.is_retrying = False
        robot.current_execution = execution
        robot.target_position = None  # never completed by heartbeat; only by command_result
        robot.deferred_cmd_payload = None
        cmd_payload = prepare_floor_change_payload(robot.name, robot.cmd_id, lift_name, robot.map_name, cabin_pose)
        robot.last_cmd_payload = cmd_payload
        robot.last_dispatch_time = time.time()
        cmd_topic = f"{self.topic_prefix}/{robot.fleet_name}/robot/{robot.name}/command"
        self._publish_command(robot, cmd_payload)
        self._publish_task_status(
            robot.fleet_name,
            robot.name,
            robot.cmd_id,
            "nav_started",
            -1,
            -1,
            f"prepare_floor_change:{lift_name}:{robot.map_name}",
        )
        print(
            f"[RMF -> AMR] Dispatched PREPARE_FLOOR_CHANGE (cmd_id: {robot.cmd_id}) to '{robot.name}' "
            f"for lift '{lift_name}' on floor '{robot.map_name}'"
        )

    def _on_rmf_navigate(self, fleet_name: str, robot_id: str, destination: Any, execution: Any):
        with self.lock:
            robot = self._get_robot(fleet_name, robot_id)
            if not robot:
                print(f"[RMF -> AMR WARN] Cannot execute NAVIGATE: robot '{robot_id}' in fleet '{fleet_name}' not found.")
                return
            if robot.session_reset_pending or not execution.okay():
                print(f"[SESSION] Suppressed stale or reset NAVIGATE for '{fleet_name}/{robot_id}'")
                return
            self._detach_active_task_locked(robot, "superseded by RMF navigate")
            target_pos = np.array(
                [destination.position[0], destination.position[1], destination.position[2]],
                dtype=np.float64,
            )
            target_map = getattr(destination, "map", robot.map_name)
            dock_name = getattr(destination, "dock", None)
            speed_limit = getattr(destination, "speed_limit", None)
            lift_name = self._resolve_destination_lift(fleet_name, destination, target_map, target_pos)
            robot.precise_arrival = bool(
                dock_name or lift_name or self._is_precise_waypoint(fleet_name, target_map, destination))
            if not lift_name:
                robot.pending_suspend_lift = None
                robot.nav_suspended = False
                robot.lift_entry_pose = None

            if not robot.is_online or robot.is_estopped or robot.is_off_graph:
                robot.pending_suspend_lift = lift_name
                reason = (
                    f"偏離路網過遠 ({robot.off_graph_distance}m > {OFF_GRAPH_THRESHOLD_M}m)，無法操控"
                    if robot.is_off_graph
                    else ("emergency-stopped" if robot.is_estopped else "offline")
                )
                self._defer_command(
                    robot, execution,
                    self._navigate_payload(robot_id, None, target_pos, target_map, dock_name, speed_limit),
                    f"Cannot execute NAVIGATE: robot '{robot_id}' in fleet '{fleet_name}' is {reason}."
                )
                return

            robot.is_holding = False
            robot.cmd_id += 1
            robot.nav_retries = 0
            robot.current_execution = execution
            robot.target_position = target_pos
            robot.deferred_cmd_payload = None
            robot.pending_suspend_lift = lift_name

            cmd_topic = f"{self.topic_prefix}/{fleet_name}/robot/{robot_id}/command"
            cmd_payload = self._navigate_payload(robot_id, robot.cmd_id, target_pos, target_map, dock_name, speed_limit)
            robot.last_cmd_payload = cmd_payload
            robot.last_dispatch_time = time.time()

            self._publish_command(robot, cmd_payload)
            self._publish_task_status(fleet_name, robot_id, robot.cmd_id, "nav_started", -1, -1, f"navigate:{target_map}:{target_pos[0]:.2f},{target_pos[1]:.2f}")
            action_label = f"DOCK ({dock_name})" if dock_name else "NAVIGATE"
            print(f"[RMF -> AMR] Dispatched {action_label} (cmd_id: {robot.cmd_id}) to '{robot_id}' via {cmd_topic}: target=[{target_pos[0]:.2f}, {target_pos[1]:.2f}, {target_pos[2]:.2f}], map='{target_map}'")

    def _default_level(self, fleet_name: str) -> str:
        levels = (getattr(self, "fleet_waypoint_info", {}).get(fleet_name) or {}).get("levels") or {}
        return DEFAULT_LEVEL if DEFAULT_LEVEL in levels or not levels else next(iter(levels))

    def _apply_floor_change_locked(self, robot: ManagedRobot, cmd_payload: Dict[str, Any]):
        """Report the new floor to RMF before finishing localize; otherwise RMF resumes the plan with the
        robot still on the old map and replans it back. The next heartbeat refines the pose."""
        pose = cmd_payload["initial_pose"]
        robot.map_name = cmd_payload["floor_name"]
        robot.position = np.array([pose["x"], pose["y"], pose["yaw"]], dtype=np.float64)
        print(f"[LIFT] Robot '{robot.name}' now localized on floor '{robot.map_name}'.")
        if not self.mock_rmf and robot.update_handle:
            try:
                activity_id = robot.current_execution.identifier if robot.current_execution else None
                robot.update_handle.update(
                    rmf_easy.RobotState(robot.map_name, robot.position, robot.battery_soc), activity_id)
            except Exception as e:
                print(f"[RMF WARN] Failed to report floor change for '{robot.name}': {e}")

    def _lift_exit_pose_locked(self, robot: ManagedRobot, floor: str, rmf_pose: List[float]) -> List[float]:
        """Initial pose for change_floor: the pose recorded at prepare_floor_change carried through the level
        transforms (keeps the robot's real heading/offset in the cabin). Falls back to RMF's cabin pose."""
        if not robot.lift_entry_pose:
            print(f"[LIFT WARN] No recorded lift entry pose for '{robot.name}'; using RMF cabin pose.")
            return rmf_pose
        src_level, entry = robot.lift_entry_pose
        levels = (getattr(self, "fleet_waypoint_info", {}).get(robot.fleet_name) or {}).get("levels") or {}
        if src_level not in levels or floor not in levels:
            print(f"[LIFT WARN] Missing level transform for '{src_level}' -> '{floor}'; using RMF cabin pose.")
            return rmf_pose
        x, y, yaw = transform_pose_between_levels(
            levels[src_level].get("transform"), levels[floor].get("transform"), entry)
        offset = math.hypot(x - rmf_pose[0], y - rmf_pose[1])
        if offset > LIFT_ENTRY_POSE_MAX_OFFSET_M:
            print(f"[LIFT WARN] Recorded pose of '{robot.name}' maps {offset:.2f} m from the '{floor}' cabin "
                  f"(> {LIFT_ENTRY_POSE_MAX_OFFSET_M} m); using RMF cabin position with the recorded heading.")
            return [rmf_pose[0], rmf_pose[1], yaw]
        print(f"[LIFT] '{robot.name}' entry pose {src_level}=[{entry[0]:.3f}, {entry[1]:.3f}, {entry[2]:.3f}] -> "
              f"{floor}=[{x:.3f}, {y:.3f}, {yaw:.3f}]")
        return [x, y, yaw]

    def _on_rmf_localize(self, fleet_name: str, robot_id: str, destination: Any, execution: Any):
        """RMF asks the robot to relocalize on a new floor (lift arrived, doors open). ``destination.position``
        is already in the target floor's robot coordinates; the initial pose sent to the robot is the pose
        recorded at prepare_floor_change mapped onto that floor (see _lift_exit_pose_locked)."""
        with self.lock:
            robot = self._get_robot(fleet_name, robot_id)
            if robot and (robot.session_reset_pending or not execution.okay()):
                print(f"[SESSION] Suppressed stale or reset LOCALIZE for '{fleet_name}/{robot_id}'")
                return
            if not robot:
                print(f"[RMF -> AMR WARN] Cannot localize: robot '{robot_id}' in fleet '{fleet_name}' not found.")
                return
            self._detach_active_task_locked(robot, "superseded by RMF localize")
            floor = str(getattr(destination, "map", "") or "")
            position = [float(v) for v in destination.position]
            if not floor:
                self._escalate_rmf_failure(robot, f"RMF localize for '{robot_id}' has no target map")
                return
            position = self._lift_exit_pose_locked(robot, floor, position)
            robot.pending_suspend_lift = None
            if not robot.is_online or robot.is_estopped:
                self._defer_command(
                    robot, execution, change_floor_payload(robot_id, 0, floor, position),
                    f"Cannot change floor: robot '{robot_id}' in fleet '{fleet_name}' is "
                    f"{'emergency-stopped' if robot.is_estopped else 'offline'}."
                )
                return
            robot.is_holding = False
            robot.cmd_id += 1
            robot.nav_retries = 0
            robot.current_execution = execution
            robot.target_position = None  # never completed by heartbeat; only by command_result
            robot.deferred_cmd_payload = None
            cmd_payload = change_floor_payload(robot_id, robot.cmd_id, floor, position)
            robot.last_cmd_payload = cmd_payload
            robot.last_dispatch_time = time.time()
            cmd_topic = f"{self.topic_prefix}/{fleet_name}/robot/{robot_id}/command"
            self._publish_command(robot, cmd_payload)
            self._publish_task_status(fleet_name, robot_id, robot.cmd_id, "nav_started", -1, -1,
                                      f"change_floor:{floor}:{position[0]:.2f},{position[1]:.2f}")
            print(f"[RMF -> AMR] Dispatched CHANGE_FLOOR (cmd_id: {robot.cmd_id}) to '{robot_id}': "
                  f"floor='{floor}', pose=[{position[0]:.2f}, {position[1]:.2f}, {position[2]:.2f}]")

    def _on_rmf_stop(self, fleet_name: str, robot_id: str, activity_id: Any):
        with self.lock:
            robot = self._get_robot(fleet_name, robot_id)
            if not robot:
                print(f"[RMF STOP WARN] Robot '{robot_id}' in '{fleet_name}' not found. Ignoring STOP.")
                return
            if robot.session_reset_pending:
                return
            if robot.session_id and robot.current_execution is None:
                return

            # Verify that activity_id matches the active execution to avoid stopping newer commands
            if robot.current_execution and hasattr(robot.current_execution, "identifier"):
                curr_id = robot.current_execution.identifier
                if curr_id is not None and activity_id is not None:
                    try:
                        if hasattr(activity_id, "is_same") and not activity_id.is_same(curr_id):
                            print(f"[RMF STOP IGNORE] Activity '{activity_id}' does not match current execution for '{robot_id}'. Ignoring stale STOP.")
                            return
                    except Exception as e:
                        print(f"[RMF STOP] is_same check warning: {e}")

            was_holding = robot.is_holding
            was_deferred = robot.deferred_cmd_payload is not None
            robot.is_holding = False
            robot.deferred_cmd_payload = None
            robot.pending_suspend_lift = None
            # The RMF activity being stopped owns the active task (if any); its final state still reaches the backend via task_refs.
            self._detach_active_task_locked(robot, "stopped by RMF")
            robot.current_execution = None
            robot.target_position = None
            robot.last_cmd_payload = None
            robot.nav_retries = 0

            if was_holding or was_deferred:
                print(f"[RMF -> AMR] Released in-place hold / deferred command for '{robot_id}' without downward STOP command.")
                return

            robot.cmd_id += 1
            robot.stop_cmd_id = robot.cmd_id
            cmd_topic = f"{self.topic_prefix}/{fleet_name}/robot/{robot_id}/command"
            cmd_payload = {
                "robot_id": robot_id,
                "cmd_id": robot.cmd_id,
                "action": "stop",
                "reason": "RMF traffic schedule hold/pause",
                "timestamp": time.time(),
            }
            self._publish_command(robot, cmd_payload)
            print(f"[RMF -> AMR] Dispatched STOP (cmd_id: {robot.cmd_id}) to '{robot_id}' via {cmd_topic}")


    def _on_rmf_action(self, fleet_name: str, robot_id: str, category: str, description: Any, execution: Any):
        with self.lock:
            robot = self._get_robot(fleet_name, robot_id)
            if not robot:
                print(f"[RMF -> AMR WARN] Cannot execute ACTION '{category}': robot '{robot_id}' in fleet '{fleet_name}' not found.")
                return
            if robot.session_reset_pending or not execution.okay():
                print(f"[SESSION] Suppressed ACTION during reset for '{fleet_name}/{robot_id}'")
                return

            # Parse description; a malformed description fails the RMF action instead of raising in the callback.
            steps: List[Dict[str, Any]] = []
            gimbal_fields: Optional[Dict[str, float]] = None
            try:
                desc_obj = json.loads(description) if isinstance(description, str) else description
                if desc_obj is None:
                    desc_obj = {}
                if category in ("gimbal_move", "gimbal_rotate"):
                    gimbal_fields = {
                        "pan_deg": float(desc_obj.get("pan_deg", desc_obj.get("pan", 0.0))),
                        "tilt_deg": float(desc_obj.get("tilt_deg", desc_obj.get("tilt", 0.0))),
                    }
                    ok, err_msg, _ = validate_task_steps([dict(gimbal_fields, type=category)])
                    if not ok:
                        raise ValueError(err_msg)
                elif category in ("task", "multi_step_task", "multi_step") or (isinstance(desc_obj, dict) and "steps" in desc_obj):
                    steps = desc_obj.get("steps", [])
                    if not steps:
                        raise ValueError("'steps' is empty")
            except Exception as e:
                robot.current_execution = execution
                self._escalate_rmf_failure(robot, f"Invalid description for RMF action '{category}' on '{robot_id}': {e}")
                return

            if steps:
                ok, err_msg, validated = validate_task_steps(steps)
                if ok and any(s["type"] == "navigate" for s in validated):
                    ok, err_msg = False, "navigate steps are not allowed inside an RMF action; use go_to_place phases so RMF schedules the traffic"
                if not ok:
                    robot.current_execution = execution
                    self._escalate_rmf_failure(robot, f"Rejected RMF action '{category}' on '{robot_id}': {err_msg}")
                    return
                self._detach_active_task_locked(robot, f"superseded by RMF action '{category}'")
                # In-place actions have no traffic impact, so the adapter releases each gate itself.
                if not robot.is_online or robot.is_estopped:
                    self._defer_command(
                        robot, execution,
                        self._task_payload(robot_id, None, validated, auto_proceed=True, task_id=f"rmf_{category}_{robot.cmd_id + 1}"),
                        f"Cannot execute ACTION '{category}': robot '{robot_id}' in fleet '{fleet_name}' is "
                        f"{'emergency-stopped' if robot.is_estopped else 'offline'}.")
                    return
                print(f"[RMF -> AMR] Routing RMF action '{category}' ({len(validated)} steps) to multi-step task for '{robot_id}'.")
                res = self._dispatch_multi_step_task_locked(
                    robot, validated, execution=execution, auto_proceed=True,
                    task_id=f"rmf_{category}_{robot.cmd_id + 1}")
                if res["status"] == "error":
                    robot.current_execution = execution
                    self._escalate_rmf_failure(robot, f"RMF action '{category}' on '{robot_id}' could not be dispatched: {res['message']}")
                return
            self._detach_active_task_locked(robot, f"superseded by RMF action '{category}'")
            if gimbal_fields is not None:
                # Gimbal actions have their own robot-side action type (like navigate), completed via command_result.
                cmd_payload = {
                    "robot_id": robot_id,
                    "cmd_id": None,
                    "action": category,
                    **gimbal_fields,
                    "timestamp": time.time(),
                }
            else:
                cmd_payload = {
                    "robot_id": robot_id,
                    "cmd_id": None,
                    "action": "action",
                    "category": category,
                    "description": description,
                    "timestamp": time.time(),
                }
            if not robot.is_online or robot.is_estopped:
                self._defer_command(
                    robot, execution, cmd_payload,
                    f"Cannot execute ACTION '{category}': robot '{robot_id}' in fleet '{fleet_name}' is "
                    f"{'emergency-stopped' if robot.is_estopped else 'offline'}.")
                return

            robot.cmd_id += 1
            cmd_payload["cmd_id"] = robot.cmd_id
            robot.current_execution = execution
            robot.target_position = None  # Clear navigation target so heartbeat cannot false-match arrival
            robot.deferred_cmd_payload = None
            cmd_topic = f"{self.topic_prefix}/{fleet_name}/robot/{robot_id}/command"
            robot.last_cmd_payload = cmd_payload
            robot.last_dispatch_time = time.time()
            self._publish_command(robot, cmd_payload)
            self._publish_task_status(fleet_name, robot_id, robot.cmd_id, "action_started", -1, -1, f"action:{category}")
            print(f"[RMF -> AMR] Dispatched ACTION '{category}' (cmd_id: {robot.cmd_id}) to '{robot_id}' via {cmd_topic}")

    # ==========================================================================
    # Background Watchdog & Main Loop
    # ==========================================================================
    def _watchdog_loop(self):
        """Monitors heartbeat timeout for registered robots and periodically syncs discovery."""
        while self.running:
            time.sleep(1.5)
            now = time.time()
            with self.lock:
                for (f_name, r_id), robot in list(self.robots.items()):
                    if robot.is_online and (now - robot.last_heartbeat > 10.0):
                        print(f"[WATCHDOG WARN] Robot '{r_id}' ({f_name}) missed heartbeats for {now - robot.last_heartbeat:.1f}s! Marking Offline.")
                        robot.is_online = False
                        robot.status = "offline"
                        if robot.current_execution:
                            print(f"[WATCHDOG WARN] Robot '{r_id}' timed out during execution (cmd_id: {robot.cmd_id}). Preserving execution without falsely marking finished.")
                        self._sync_rmf_availability(robot)

                for k, disc in list(self.discovered_robots.items()):
                    if disc.get("status") != "offline" and (now - disc.get("last_seen", 0) > 15.0):
                        disc["status"] = "offline"

                self._save_discovered_robots()
            self._publish_fa_state()

    def start(self):
        """Starts MQTT connection, background threads, and spins ROS 2."""
        # Connect MQTT in a background network loop
        self.mqtt_client.connect(self.mqtt_host, self.mqtt_port, self.keepalive)
        self.mqtt_client.loop_start()

        # Start Heartbeat Watchdog Thread
        watchdog_thread = threading.Thread(target=self._watchdog_loop, daemon=True)
        watchdog_thread.start()

        print(f"✅ MQTT Fleet Adapter running. Press Ctrl+C to terminate.")

        if not self.mock_rmf and self.node:
            try:
                rclpy.spin(self.node)
            except KeyboardInterrupt:
                pass
        else:
            # Standalone spin loop
            try:
                while self.running:
                    time.sleep(0.5)
            except KeyboardInterrupt:
                pass

        self.shutdown()

    def shutdown(self):
        """Cleans up resources gracefully."""
        print("[SHUTDOWN] Terminating MQTT Fleet Adapter...")
        self.running = False
        self.mqtt_client.loop_stop()
        self.mqtt_client.disconnect()
        if not self.mock_rmf and self.node:
            self.node.destroy_node()
            rclpy.shutdown()
        print("[SHUTDOWN] Completed.")


def main():
    parser = argparse.ArgumentParser(description="Open-RMF MQTT Fleet Adapter")
    parser.add_argument(
        "-c", "--config",
        default=os.path.join(os.path.dirname(__file__), "config.yaml"),
        help="Path to fleet config YAML",
    )
    parser.add_argument(
        "-n", "--nav_graph",
        default=find_backend_path("saved_maps/nav_graphs/0.yaml"),
        help="Path to nav graph YAML",
    )
    parser.add_argument(
        "--mqtt_host",
        default=os.environ.get("MQTT_BROKER_HOST", "127.0.0.1"),
        help="MQTT Broker Host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--mqtt_port",
        type=int,
        default=int(os.environ.get("MQTT_BROKER_PORT", 1883)),
        help="MQTT Broker Port (default: 1883)",
    )
    parser.add_argument(
        "--standalone", "--mock",
        action="store_true",
        dest="mock",
        help="Run in standalone mock mode (skips live ROS 2 RMF Core connection)",
    )

    args = parser.parse_args()

    adapter = MqttFleetAdapter(
        config_path=args.config,
        nav_graph_path=args.nav_graph,
        mqtt_host=args.mqtt_host,
        mqtt_port=args.mqtt_port,
        mock_rmf=args.mock,
    )
    adapter.start()


if __name__ == "__main__":
    main()
