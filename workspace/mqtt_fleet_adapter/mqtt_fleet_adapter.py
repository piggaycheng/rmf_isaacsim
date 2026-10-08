#!/usr/bin/env python3
# ==============================================================================
# Open-RMF MQTT Fleet Adapter (EasyFullControl)
# Connects AMR fleets via MQTT and bridges them to the Open-RMF core scheduler.
# ==============================================================================

import argparse
import json
import math
import os
import sys
import threading
import time
from typing import Dict, Optional, Any

import numpy as np
import paho.mqtt.client as mqtt
import yaml

# ROS 2 & Open-RMF imports
try:
    import rclpy
    from rclpy.node import Node
    from rclpy.parameter import Parameter
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
    return candidates[0]


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
        self.is_online = True

        # Navigation & Activity Execution State
        self.cmd_id = 0
        self.current_execution = None
        self.target_position: Optional[np.ndarray] = None
        self.is_holding: bool = False
        self.nav_retries: int = 0
        self.last_cmd_payload: Optional[Dict[str, Any]] = None


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

        self.robots: Dict[str, ManagedRobot] = {}
        self.lock = threading.Lock()
        self.running = True

        print(f"==================================================")
        print(f"🚀 Initializing MQTT Fleet Adapter: [{self.fleet_name}]")
        print(f"   Config:    {self.config_path}")
        print(f"   Nav Graph: {self.nav_graph_path}")
        print(f"   MQTT:      {self.mqtt_host}:{self.mqtt_port}")
        print(f"   Mode:      {'MOCK / STANDALONE' if self.mock_rmf else 'LIVE OPEN-RMF'}")
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

    def _init_rmf(self):
        """Initializes ROS 2 and Open-RMF Adapter."""
        try:
            rclpy.init()
            rmf_adapter.init_rclcpp()

            self.node = rclpy.node.Node("rmf_mqtt_command_handle")
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

            for item in adapters_data:
                f_name = item.get("fleet_name") or self.fleet_name
                g_idx = item.get("graph_idx", 0)
                nav_path = find_backend_path(f"saved_maps/nav_graphs/{g_idx}.yaml")
                if not os.path.exists(nav_path):
                    nav_path = self.nav_graph_path

                try:
                    cfg = rmf_easy.FleetConfiguration.from_config_files(self.config_path, nav_path)
                    if cfg:
                        cfg.fleet_name = f_name
                        handle = self.adapter.add_easy_fleet(cfg)
                        self.fleet_handles[f_name] = handle
                        print(f"[RMF] Successfully registered fleet [{f_name}] (Graph {g_idx}) into Open-RMF!")
                except Exception as e:
                    print(f"[ERROR] Failed to register fleet [{f_name}]: {e}")

            if self.fleet_handles:
                self.fleet_handle = list(self.fleet_handles.values())[0]
            else:
                self.fleet_config = rmf_easy.FleetConfiguration.from_config_files(self.config_path, self.nav_graph_path)
                self.fleet_handle = self.adapter.add_easy_fleet(self.fleet_config)
                self.fleet_handles[self.fleet_name] = self.fleet_handle

            print(f"[INFO] Open-RMF EasyFullControl Adapter ready for fleets: {list(self.fleet_handles.keys())}")
        except Exception as e:
            print(f"[ERROR] Failed to start live Open-RMF Adapter: {e}")
            print("[WARN] Switching to Mock/Standalone mode.")
            self.mock_rmf = True

    # ==========================================================================
    # MQTT Callbacks & Connection
    # ==========================================================================
    def _on_mqtt_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print(f"[MQTT] Connected successfully to Broker at {self.mqtt_host}:{self.mqtt_port}")
            # Wildcard subscription to support both self.fleet_name and all other AMR fleets
            client.subscribe(f"{self.topic_prefix}/+/robot/+/register", qos=1)
            client.subscribe(f"{self.topic_prefix}/+/robot/+/heartbeat", qos=0)
            client.subscribe(f"{self.topic_prefix}/+/robot/+/deregister", qos=1)
            client.subscribe(f"{self.topic_prefix}/+/robot/+/status", qos=1)
            client.subscribe(f"{self.topic_prefix}/+/robot/+/command_result", qos=1)
            print(f"[MQTT] Subscribed to wildcard -> {self.topic_prefix}/+/robot/+/*")
        else:
            print(f"[MQTT] Connection failed with result code: {rc}")

    def _on_mqtt_disconnect(self, client, userdata, rc):
        if rc != 0:
            print(f"[MQTT] Unexpected disconnection (rc={rc}). Reconnecting...")

    def _on_mqtt_message(self, client, userdata, msg):
        topic = msg.topic
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception as e:
            print(f"[MQTT ERROR] Invalid JSON received on topic {topic}: {e}")
            return

        # Extract fleet_name & robot_id from topic: rmf/{fleet_name}/robot/{robot_id}/{action}
        parts = topic.split("/")
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

    # ==========================================================================
    # Handler 1: Robot Registration (開機註冊)
    # ==========================================================================
    def _handle_register(self, robot_id: str, fleet_name: str, payload: Dict[str, Any]):
        print(f"[REGISTER] Received registration request for robot '{robot_id}' in fleet '{fleet_name}': {payload}")

        loc = payload.get("initial_location", {})
        x = float(loc.get("x", 0.0))
        y = float(loc.get("y", 0.0))
        yaw = float(loc.get("yaw", 0.0))
        level_name = loc.get("level_name", "L1")
        fallback_charger = "wp_1"
        default_charger = payload.get("default_charger") or fallback_charger
        default_parking = payload.get("default_parking") or fallback_charger
        initial_pos = np.array([x, y, yaw], dtype=np.float64)

        with self.lock:
            # Check if already registered
            if robot_id in self.robots and self.robots[robot_id].is_online and (self.mock_rmf or self.robots[robot_id].update_handle is not None):
                print(f"[REGISTER] Robot '{robot_id}' already registered with active RMF handle. Acknowledging again.")
                self._send_register_ack(robot_id, success=True, msg="Robot already registered", fleet_name=fleet_name)
                return

            # Live Open-RMF Registration
            update_handle = None
            target_handle = self.fleet_handles.get(fleet_name) or self.fleet_handle
            if not self.mock_rmf and target_handle:
                try:
                    initial_state = rmf_easy.RobotState(level_name, initial_pos, 1.0)
                    robot_config = rmf_easy.RobotConfiguration([default_charger])

                    callbacks = rmf_easy.RobotCallbacks(
                        lambda dest, execution: self._on_rmf_navigate(robot_id, dest, execution),
                        lambda act_id: self._on_rmf_stop(robot_id, act_id),
                        lambda cat, desc, exec_h: self._on_rmf_action(robot_id, cat, desc, exec_h),
                    )

                    update_handle = target_handle.add_robot(
                        robot_id, initial_state, robot_config, callbacks
                    )
                    print(f"[RMF] Successfully registered '{robot_id}' into Open-RMF fleet [{fleet_name}] handle!")
                except Exception as e:
                    print(f"[ERROR] Failed to add robot to RMF core: {e}")
                    self._send_register_ack(robot_id, success=False, msg=str(e), fleet_name=fleet_name)
                    return

            # Store in Managed Robots dictionary
            managed_robot = ManagedRobot(
                name=robot_id,
                fleet_name=fleet_name,
                map_name=level_name,
                position=initial_pos,
                battery_soc=1.0,
                update_handle=update_handle,
                charger_waypoint=default_charger,
            )
            self.robots[robot_id] = managed_robot

        # Send ACK back to AMR
        self._send_register_ack(robot_id, success=True, msg="Registered successfully", fleet_name=fleet_name)

    def _send_register_ack(self, robot_id: str, success: bool, msg: str, status: Optional[str] = None, fleet_name: Optional[str] = None):
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
        self.mqtt_client.publish(ack_topic, json.dumps(ack_payload), qos=1)
        print(f"[REGISTER] Sent ACK to '{robot_id}' on {ack_topic}: {ack_payload}")

    # ==========================================================================
    # Handler 2: Periodic Heartbeat (週期性遙測心跳)
    # ==========================================================================
    def _handle_heartbeat(self, robot_id: str, fleet_name: str, payload: Dict[str, Any]):
        robot_id = payload.get("robot_id") or robot_id
        with self.lock:
            robot = self.robots.get(robot_id)
            if not robot or (not self.mock_rmf and robot.update_handle is None):
                # Robot sent heartbeat without registering first (e.g. FA late startup or restart)
                print(f"[WARN] Heartbeat from robot '{robot_id}' without active RMF handle ({fleet_name}). Requesting registration...")
                self._send_register_ack(robot_id, success=False, msg="Robot not registered in RMF, please register", status="require_register", fleet_name=fleet_name)
                return

            # Update telemetry
            x = float(payload.get("x", robot.position[0]))
            y = float(payload.get("y", robot.position[1]))
            yaw = float(payload.get("yaw", robot.position[2]))
            battery = float(payload.get("battery", robot.battery_soc * 100.0))
            level_name = payload.get("level_name", robot.map_name)
            status = payload.get("status", "idle")

            robot.position = np.array([x, y, yaw], dtype=np.float64)
            robot.battery_soc = max(0.0, min(1.0, battery / 100.0))
            robot.map_name = level_name
            robot.status = status
            robot.last_heartbeat = time.time()
            robot.is_online = True

            # Check if active navigation command reached its destination (ignore if in-place holding)
            reported_cmd_id = payload.get("current_cmd_id")
            if robot.current_execution and robot.target_position is not None and not robot.is_holding:
                dx = robot.target_position[0] - x
                dy = robot.target_position[1] - y
                dist = math.hypot(dx, dy)

                # Only mark completed via heartbeat if robot has stopped (status == 'idle') near destination.
                # NEVER declare finished while status == 'moving', because that prematurely dispatches
                # the next waypoint, interrupting active Nav2 execution and causing subsequent steps to abort!
                cmd_matches = (reported_cmd_id is not None and reported_cmd_id == robot.cmd_id)
                if status == "idle" and (dist < 0.25 or (cmd_matches and dist < 0.40)):
                    print(f"[NAV] Robot '{robot_id}' completed cmd_id {robot.cmd_id} (dist: {dist:.3f}m, status: {status}). Finishing execution.")
                    try:
                        robot.current_execution.finished()
                    except Exception as e:
                        print(f"[NAV NOTICE] Execution finish exception: {e}")
                    robot.current_execution = None
                    robot.target_position = None
                    robot.last_cmd_payload = None
                    robot.nav_retries = 0

            # Forward state update to Open-RMF core
            if not self.mock_rmf and robot.update_handle:
                try:
                    rmf_state = rmf_easy.RobotState(robot.map_name, robot.position, robot.battery_soc)
                    activity_id = (
                        robot.current_execution.identifier
                        if robot.current_execution
                        else None
                    )
                    robot.update_handle.update(rmf_state, activity_id)
                except Exception as e:
                    print(f"[ERROR] rmf update error for '{robot_id}': {e}")

    # ==========================================================================
    # Handler 3: Deregister & Status (正常離線與異常斷線 LWT)
    # ==========================================================================
    def _handle_deregister(self, robot_id: str, fleet_name: str, payload: Dict[str, Any]):
        print(f"[DEREGISTER] Robot '{robot_id}' in '{fleet_name}' requested deregistration: {payload}")
        with self.lock:
            if robot_id in self.robots:
                robot = self.robots[robot_id]
                robot.is_online = False
                if robot.current_execution:
                    try:
                        robot.current_execution.finished()
                    except Exception:
                        pass
                    robot.current_execution = None
                del self.robots[robot_id]
                print(f"[DEREGISTER] Robot '{robot_id}' cleanly removed from active fleet.")

    def _handle_status(self, robot_id: str, fleet_name: str, payload: Dict[str, Any]):
        status = payload.get("status", "unknown")
        print(f"[STATUS/LWT] Robot '{robot_id}' ({fleet_name}) reported status: {status}")
        if status in ["offline", "disconnected"]:
            with self.lock:
                if robot_id in self.robots:
                    self.robots[robot_id].is_online = False
                    print(f"[ALERT] Robot '{robot_id}' marked OFFLINE due to status message.")

    def _handle_command_result(self, robot_id: str, fleet_name: str, payload: Dict[str, Any]):
        cmd_id = payload.get("cmd_id")
        status = payload.get("status", "completed")
        print(f"[CMD_RESULT] Robot '{robot_id}' ({fleet_name}) reported cmd_id {cmd_id} status: {status}")

        with self.lock:
            robot = self.robots.get(robot_id)
            if robot and robot.current_execution and not robot.is_holding:
                if cmd_id == robot.cmd_id and status in ["completed", "success"]:
                    print(f"[NAV] Verified completion of cmd_id {cmd_id} for '{robot_id}'.")
                    try:
                        robot.current_execution.finished()
                    except Exception as e:
                        print(f"[NOTICE] Finish callback notice: {e}")
                    robot.current_execution = None
                    robot.target_position = None
                    robot.last_cmd_payload = None
                    robot.nav_retries = 0
                elif cmd_id == robot.cmd_id and status in ["failed", "aborted"]:
                    print(f"[NAV WARN] Robot '{robot_id}' reported failure for cmd_id {cmd_id} (status: {status}).")
                    if getattr(robot, "nav_retries", 0) < 2 and robot.last_cmd_payload:
                        robot.nav_retries = getattr(robot, "nav_retries", 0) + 1
                        print(f"[NAV RETRY] Scheduling retry for cmd_id {cmd_id} on '{robot_id}' (attempt {robot.nav_retries}/2) in 1.2s...")
                        def retry_nav():
                            with self.lock:
                                if robot.current_execution and robot.cmd_id == cmd_id and robot.last_cmd_payload:
                                    cmd_topic = f"{self.topic_prefix}/{robot.fleet_name}/robot/{robot_id}/command"
                                    payload_to_send = dict(robot.last_cmd_payload)
                                    payload_to_send["timestamp"] = time.time()
                                    self.mqtt_client.publish(cmd_topic, json.dumps(payload_to_send), qos=1)
                                    print(f"[NAV RETRY] Re-dispatched cmd_id {cmd_id} to '{robot_id}'")
                        threading.Timer(1.2, retry_nav).start()

    # ==========================================================================
    # RMF -> Robot Outbound Callbacks (調度下發指令)
    # ==========================================================================
    def _on_rmf_navigate(self, robot_id: str, destination: Any, execution: Any):
        with self.lock:
            robot = self.robots.get(robot_id)
            if not robot:
                return

            target_pos = np.array(
                [destination.position[0], destination.position[1], destination.position[2]],
                dtype=np.float64,
            )
            dx = target_pos[0] - robot.position[0]
            dy = target_pos[1] - robot.position[1]
            dist = math.hypot(dx, dy)

            # If robot is already at destination (< 0.20m):
            # Complete execution smoothly so Open-RMF knows this waypoint/step is fulfilled.
            if dist < 0.20:
                print(f"[RMF -> AMR] Robot '{robot_id}' already at destination [{target_pos[0]:.2f}, {target_pos[1]:.2f}] (dist: {dist:.3f}m < 0.2m). Fulfilling in-place execution.")
                robot.current_execution = execution
                robot.target_position = target_pos
                robot.is_holding = False

                def finish_in_place():
                    with self.lock:
                        if robot.current_execution == execution:
                            try:
                                execution.finished()
                                print(f"[NAV] Completed in-place navigation for '{robot_id}'.")
                            except Exception as ex:
                                print(f"[NAV NOTICE] In-place finish notice: {ex}")
                            robot.current_execution = None
                            robot.target_position = None

                threading.Timer(0.3, finish_in_place).start()
                return

            robot.is_holding = False
            robot.cmd_id += 1
            robot.nav_retries = 0
            robot.current_execution = execution
            robot.target_position = target_pos

            dock_name = getattr(destination, "dock", None)
            speed_limit = getattr(destination, "speed_limit", None)

            cmd_topic = f"{self.topic_prefix}/{robot.fleet_name}/robot/{robot_id}/command"
            cmd_payload = {
                "robot_id": robot_id,
                "cmd_id": robot.cmd_id,
                "action": "dock" if dock_name else "navigate",
                "target": {
                    "x": float(target_pos[0]),
                    "y": float(target_pos[1]),
                    "yaw": float(target_pos[2]),
                    "level_name": destination.map,
                },
                "dock_name": dock_name,
                "speed_limit": speed_limit,
                "timestamp": time.time(),
            }
            robot.last_cmd_payload = cmd_payload

            self.mqtt_client.publish(cmd_topic, json.dumps(cmd_payload), qos=1)
            print(f"[RMF -> AMR] Dispatched NAVIGATE (cmd_id: {robot.cmd_id}) to '{robot_id}': target=[{target_pos[0]:.2f}, {target_pos[1]:.2f}, {target_pos[2]:.2f}]")

    def _on_rmf_stop(self, robot_id: str, activity_id: Any):
        with self.lock:
            robot = self.robots.get(robot_id)
            if not robot:
                return

            was_holding = robot.is_holding
            robot.is_holding = False
            if was_holding:
                robot.current_execution = None
                robot.target_position = None
                print(f"[RMF -> AMR] Released in-place hold for '{robot_id}' without downward STOP command.")
                return

            robot.cmd_id += 1
            cmd_topic = f"{self.topic_prefix}/{robot.fleet_name}/robot/{robot_id}/command"
            cmd_payload = {
                "robot_id": robot_id,
                "cmd_id": robot.cmd_id,
                "action": "stop",
                "reason": "RMF traffic schedule hold/pause",
                "timestamp": time.time(),
            }
            self.mqtt_client.publish(cmd_topic, json.dumps(cmd_payload), qos=1)
            print(f"[RMF -> AMR] Dispatched STOP to '{robot_id}'")


    def _on_rmf_action(self, robot_id: str, category: str, description: Any, execution: Any):
        with self.lock:
            robot = self.robots.get(robot_id)
            if not robot:
                return

            robot.cmd_id += 1
            robot.current_execution = execution
            cmd_topic = f"{self.topic_prefix}/{self.fleet_name}/robot/{robot_id}/command"
            cmd_payload = {
                "robot_id": robot_id,
                "cmd_id": robot.cmd_id,
                "action": "action",
                "category": category,
                "description": description,
                "timestamp": time.time(),
            }
            self.mqtt_client.publish(cmd_topic, json.dumps(cmd_payload), qos=1)
            print(f"[RMF -> AMR] Dispatched ACTION '{category}' to '{robot_id}'")

    # ==========================================================================
    # Background Watchdog & Main Loop
    # ==========================================================================
    def _watchdog_loop(self):
        """Monitors heartbeat timeout for registered robots."""
        while self.running:
            time.sleep(2.0)
            now = time.time()
            with self.lock:
                for r_id, robot in list(self.robots.items()):
                    if robot.is_online and (now - robot.last_heartbeat > 10.0):
                        print(f"[WATCHDOG WARN] Robot '{r_id}' missed heartbeats for {now - robot.last_heartbeat:.1f}s! Marking Offline.")
                        robot.is_online = False
                        if robot.current_execution:
                            try:
                                robot.current_execution.finished()
                            except Exception:
                                pass
                            robot.current_execution = None

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
