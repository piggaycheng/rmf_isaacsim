#!/usr/bin/env python3
# ==============================================================================
# Mock AMR Client (模擬車輛端 MQTT 客戶端)
# Demonstrates how AMR onboard software interacts with the Open-RMF MQTT Fleet Adapter.
# ==============================================================================

import argparse
import json
import math
import signal
import sys
import time
import threading
import uuid
import paho.mqtt.client as mqtt

class MockRobot:
    def __init__(
        self,
        robot_id: str = "mock_amr_01",
        fleet_name: str = "tinyRobot",
        mqtt_host: str = "127.0.0.1",
        mqtt_port: int = 1883,
        start_x: float = 0.7,
        start_y: float = 0.9,
        start_yaw: float = 0.0,
        level: str = "1F",
    ):
        self.robot_id = robot_id
        self.session_id = str(uuid.uuid4())
        self.fleet_name = fleet_name
        self.mqtt_host = mqtt_host
        self.mqtt_port = mqtt_port

        # Current Robot State
        self.x = start_x
        self.y = start_y
        self.yaw = start_yaw
        self.level = level
        self.battery = 98.0
        self.status = "idle" # idle, moving, charging, error
        self.current_cmd_id = None
        self.is_registered = False
        self.running = True

        # Target Navigation State
        self.target_x = None
        self.target_y = None
        self.target_yaw = None
        self.target_speed = 1.0
        self.nav_suspended = False

        # Topic paths
        self.base_topic = f"rmf/{self.fleet_name}/robot/{self.robot_id}"
        self.topic_register = f"{self.base_topic}/register"
        self.topic_register_ack = f"{self.base_topic}/register_ack"
        self.topic_heartbeat = f"{self.base_topic}/heartbeat"
        self.topic_command = f"{self.base_topic}/command"
        self.topic_cmd_result = f"{self.base_topic}/command_result"
        self.topic_deregister = f"{self.base_topic}/deregister"
        self.topic_status = f"{self.base_topic}/status"

        # Setup MQTT Client
        client_id = f"amr_{self.fleet_name}_{self.robot_id}"
        self.client = mqtt.Client(client_id=client_id)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        # Configure MQTT Last Will and Testament (LWT)
        lwt_payload = json.dumps({
            "robot_id": self.robot_id,
            "session_id": self.session_id,
            "status": "offline",
            "reason": "Unexpected connection loss (LWT)",
            "timestamp": time.time()
        })
        self.client.will_set(self.topic_status, lwt_payload, qos=1, retain=False)

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print(f"[AMR] Connected to MQTT Broker at {self.mqtt_host}:{self.mqtt_port}")
            # Subscribe to command and ACK topics
            client.subscribe(self.topic_register_ack, qos=1)
            client.subscribe(self.topic_command, qos=1)
            print(f"[AMR] Subscribed to ACK & COMMAND topics")

            if not self.is_registered:
                self.send_registration()
        else:
            print(f"[AMR ERROR] MQTT connection failed: {rc}")

    def send_registration(self):
        """Sends registration packet to Fleet Adapter."""
        payload = {
            "robot_id": self.robot_id,
            "session_id": self.session_id,
            "fleet_name": self.fleet_name,
            "timestamp": time.time(),
            "initial_location": {
                "x": self.x,
                "y": self.y,
                "yaw": self.yaw,
                "level_name": self.level,
                "waypoint_name": "wp_1"
            },
            "specs": {
                "footprint_radius": 0.35,
                "max_linear_velocity": 1.2,
                "max_angular_velocity": 1.0
            },
            "default_charger": "wp_1",
            "default_parking": "wp_1"
        }
        print(f"[AMR] Sending Registration -> {self.topic_register}")
        self.client.publish(self.topic_register, json.dumps(payload), qos=1)

    def on_message(self, client, userdata, msg):
        topic = msg.topic
        payload = json.loads(msg.payload.decode("utf-8"))

        if topic == self.topic_register_ack:
            if payload.get("session_id") != self.session_id:
                return
            status = payload.get("status")
            if status == "success":
                print(f"[AMR] ✅ Registration SUCCESSFUL! Adapter acknowledged: {payload.get('message')}")
                self.is_registered = True
            elif status == "require_register":
                print(f"[AMR] ⚠️ FA requested re-registration (status: require_register). Re-sending /register...")
                self.is_registered = False
                self.send_registration()
            else:
                print(f"[AMR] ❌ Registration REJECTED: {payload.get('message')}")

        elif topic == self.topic_command:
            self.handle_command(payload)

    def handle_command(self, cmd: dict):
        if not self.is_registered or cmd.get("session_id") != self.session_id:
            return
        target_robot = cmd.get("robot_id")
        if target_robot and target_robot != self.robot_id:
            return  # Not for this robot

        action = cmd.get("action")
        cmd_id = cmd.get("cmd_id")
        print(f"\n[AMR] 📥 Received COMMAND for '{target_robot or self.robot_id}' (cmd_id={cmd_id}, action={action}): {cmd}")

        if action in ["navigate", "dock"]:
            target = cmd.get("target", {})
            self.nav_suspended = False
            self.target_x = float(target.get("x", self.x))
            self.target_y = float(target.get("y", self.y))
            self.target_yaw = float(target.get("yaw", self.yaw))
            self.target_speed = float(cmd.get("speed_limit") or 1.0)
            self.current_cmd_id = cmd_id
            self.status = "moving"
            print(f"[AMR] 🚗 Starting movement towards: ({self.target_x:.2f}, {self.target_y:.2f})")

        elif action in ("prepare_floor_change", "suspend_navigation"):
            lift_name = cmd.get("lift_name", "lift")
            pose = cmd.get("pose") or {}
            ok = self.status != "moving"
            if ok:
                self.nav_suspended = True
                if all(k in pose for k in ("x", "y", "yaw")):
                    self.x, self.y, self.yaw = float(pose["x"]), float(pose["y"]), float(pose["yaw"])
                print(f"[AMR] 🛗🔒 Prepared floor change (suspended navigation) inside lift '{lift_name}' on floor '{self.level}'")
            self.client.publish(self.topic_cmd_result, json.dumps({
                "robot_id": self.robot_id,
                "session_id": self.session_id,
                "cmd_id": cmd_id,
                "status": "completed" if ok else "failed",
                "message": f"Prepared floor change in {lift_name}" if ok else "Cannot prepare floor change while moving",
                "final_location": {"x": self.x, "y": self.y, "yaw": self.yaw, "level_name": self.level},
                "timestamp": time.time(),
            }), qos=1)

        elif action == "change_floor":
            # Robot-side extension: reload the floor map, relocalize, and re-enable navigation (simulated as instant).
            pose = cmd.get("initial_pose") or {}
            floor = cmd.get("floor_name")
            ok = bool(floor) and all(k in pose for k in ("x", "y", "yaw")) and self.status != "moving"
            if ok:
                self.level = floor
                self.x, self.y, self.yaw = float(pose["x"]), float(pose["y"]), float(pose["yaw"])
                self.nav_suspended = False
                print(f"[AMR] 🛗🔓 Switched to floor '{floor}' at ({self.x:.2f}, {self.y:.2f}, {self.yaw:.2f}) and resumed navigation")
            self.client.publish(self.topic_cmd_result, json.dumps({
                "robot_id": self.robot_id,
                "session_id": self.session_id,
                "cmd_id": cmd_id,
                "status": "completed" if ok else "failed",
                "message": f"Floor switched to {floor}" if ok else "Invalid change_floor or robot is moving",
                "final_location": {"x": self.x, "y": self.y, "yaw": self.yaw, "level_name": self.level},
                "timestamp": time.time(),
            }), qos=1)

        elif action == "stop":
            print(f"[AMR] 🛑 STOP command executed.")
            self.status = "idle"
            self.target_x = None
            self.target_y = None

    def motion_loop(self):
        """Simulates vehicle physics / movement towards target."""
        while self.running:
            time.sleep(0.1) # 10 Hz physics update
            if self.status == "moving" and self.target_x is not None:
                dx = self.target_x - self.x
                dy = self.target_y - self.y
                dist = math.hypot(dx, dy)

                if dist < 0.08:
                    # Arrived at destination!
                    self.x = self.target_x
                    self.y = self.target_y
                    self.yaw = self.target_yaw
                    self.status = "idle"

                    print(f"\n[AMR] 🎯 ARRIVED at target! Reporting command_result...")
                    result_payload = {
                        "robot_id": self.robot_id,
                        "session_id": self.session_id,
                        "cmd_id": self.current_cmd_id,
                        "status": "completed",
                        "message": "Reached destination successfully",
                        "final_location": {"x": self.x, "y": self.y, "yaw": self.yaw},
                        "timestamp": time.time()
                    }
                    self.client.publish(self.topic_cmd_result, json.dumps(result_payload), qos=1)

                    self.target_x = None
                    self.target_y = None
                    self.current_cmd_id = None
                else:
                    # Move step
                    step = min(dist, self.target_speed * 0.1)
                    angle = math.atan2(dy, dx)
                    self.x += math.cos(angle) * step
                    self.y += math.sin(angle) * step
                    self.yaw = angle
                    self.battery = max(5.0, self.battery - 0.005)

    def heartbeat_loop(self):
        """Sends periodic telemetry heartbeat at 2 Hz."""
        while self.running:
            time.sleep(0.5) # 2 Hz
            if not self.is_registered:
                continue

            hb_payload = {
                "robot_id": self.robot_id,
                "session_id": self.session_id,
                "localization_ready": True,
                "x": round(self.x, 3),
                "y": round(self.y, 3),
                "yaw": round(self.yaw, 3),
                "battery": round(self.battery, 1),
                "status": self.status,
                "current_cmd_id": self.current_cmd_id,
                "level_name": self.level,
            }
            self.client.publish(self.topic_heartbeat, json.dumps(hb_payload), qos=0)

    def registration_retry_loop(self):
        """Retries registration periodically until ACK is received."""
        while self.running:
            time.sleep(3.0)
            if not self.is_registered:
                print("[AMR] ⏳ Waiting for register_ack timeout (3s). Re-trying registration...")
                self.send_registration()

    def start(self):
        self.client.connect(self.mqtt_host, self.mqtt_port, 60)
        self.client.loop_start()

        # Start motion, heartbeat, and registration retry worker threads
        t_motion = threading.Thread(target=self.motion_loop, daemon=True)
        t_heartbeat = threading.Thread(target=self.heartbeat_loop, daemon=True)
        t_reg_retry = threading.Thread(target=self.registration_retry_loop, daemon=True)
        t_motion.start()
        t_heartbeat.start()
        t_reg_retry.start()

        print(f"[AMR] Mock AMR running. Press Ctrl+C to stop.")
        try:
            while self.running:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        self.shutdown()

    def shutdown(self):
        print("\n[AMR] Shutting down. Sending deregister...")
        self.running = False
        dereg_payload = {
            "robot_id": self.robot_id,
            "session_id": self.session_id,
            "fleet_name": self.fleet_name,
            "reason": "Graceful shutdown",
            "timestamp": time.time()
        }
        self.client.publish(self.topic_deregister, json.dumps(dereg_payload), qos=1)
        time.sleep(0.5)
        self.client.loop_stop()
        self.client.disconnect()
        print("[AMR] Disconnected cleanly.")

def main():
    parser = argparse.ArgumentParser(description="Mock AMR MQTT Client")
    parser.add_argument("--id", default="mock_amr_01", help="Robot ID")
    parser.add_argument("--fleet", default="tinyRobot", help="Fleet Name")
    parser.add_argument("--host", default="127.0.0.1", help="MQTT Broker Host")
    parser.add_argument("--port", type=int, default=1883, help="MQTT Broker Port")
    parser.add_argument("--x", type=float, default=0.7, help="Initial X")
    parser.add_argument("--y", type=float, default=0.9, help="Initial Y")
    parser.add_argument("--yaw", type=float, default=0.0, help="Initial yaw (rad)")
    parser.add_argument("--level", default="1F", help="Initial floor / RMF level name")
    args = parser.parse_args()

    robot = MockRobot(
        robot_id=args.id,
        fleet_name=args.fleet,
        mqtt_host=args.host,
        mqtt_port=args.port,
        start_x=args.x,
        start_y=args.y,
        start_yaw=args.yaw,
        level=args.level,
    )
    robot.start()

if __name__ == "__main__":
    main()
