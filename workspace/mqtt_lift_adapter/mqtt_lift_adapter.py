#!/usr/bin/env python3
"""Bridge Open-RMF lifts to MQTT lift controllers (e.g. the Isaac Sim Office elevator).

ROS 2 side (with `ros2 run rmf_fleet_adapter lift_supervisor` in between):
    /lift_requests (rmf_lift_msgs/LiftRequest)  -> MQTT <prefix>/<lift>/request
    MQTT <prefix>/<lift>/state                  -> /lift_states (rmf_lift_msgs/LiftState)

MQTT JSON (same fields as rmf_lift_msgs, enums as lowercase strings):
    request: {"session_id", "request_type": "agv"|"human"|"end_session",
              "destination_floor", "door_state": "open"|"closed", "request_time"}
    state:   {"lift_name", "available_floors", "current_floor", "destination_floor",
              "door_state": "closed"|"moving"|"open", "motion_state": "stopped"|"up"|"down",
              "current_mode": "human"|"agv"|"offline", "session_id"}
Lifts are discovered from their retained state. A lift whose state is older than
--stale_after seconds is reported in MODE_OFFLINE so Open-RMF does not use it.
"""

import argparse
import json
import os
import threading
import time

import paho.mqtt.client as mqtt

REQUEST_TYPES = {0: "end_session", 1: "agv", 2: "human"}
DOOR_TO_ROS = {"closed": 0, "moving": 1, "open": 2}
MOTION_TO_ROS = {"stopped": 0, "up": 1, "down": 2}
MODE_TO_ROS = {"human": 1, "agv": 2, "fire": 3, "offline": 4, "emergency": 5}


def request_to_json(msg):
    return {
        "session_id": msg.session_id,
        "request_type": REQUEST_TYPES.get(msg.request_type, "end_session"),
        "destination_floor": msg.destination_floor,
        # LiftRequest only uses CLOSED (0) or OPEN (2).
        "door_state": "open" if msg.door_state == 2 else "closed",
        "request_time": msg.request_time.sec + msg.request_time.nanosec * 1e-9,
    }


def fill_state(msg, data, stamp, offline=False):
    """Fill a LiftState message from MQTT JSON; unknown values become UNKNOWN/OFFLINE."""
    msg.lift_time = stamp
    msg.lift_name = str(data.get("lift_name", ""))
    msg.available_floors = [str(f) for f in data.get("available_floors", [])]
    msg.current_floor = str(data.get("current_floor", ""))
    msg.destination_floor = str(data.get("destination_floor", msg.current_floor))
    msg.door_state = DOOR_TO_ROS.get(data.get("door_state"), 0)
    msg.motion_state = MOTION_TO_ROS.get(data.get("motion_state"), 3)
    msg.available_modes = [1, 2]
    msg.current_mode = 4 if offline else MODE_TO_ROS.get(data.get("current_mode"), 0)
    msg.session_id = str(data.get("session_id", ""))
    return msg


def make_client(client_id):
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
    except AttributeError:  # paho-mqtt < 2.0
        return mqtt.Client(client_id=client_id)


class LiftBridge:
    def __init__(self, node, host, port, prefix, username=None, password=None,
                 stale_after=3.0, clock=time.monotonic):
        from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
        from rmf_lift_msgs.msg import LiftRequest, LiftState

        self.node, self.prefix, self.stale_after, self.clock = node, prefix.strip("/"), stale_after, clock
        self.LiftState = LiftState
        self.states = {}  # lift name -> (json, receive time)
        self.lock = threading.Lock()
        transient = QoSProfile(depth=100, reliability=ReliabilityPolicy.RELIABLE,
                               durability=DurabilityPolicy.TRANSIENT_LOCAL)
        volatile = QoSProfile(depth=100, reliability=ReliabilityPolicy.RELIABLE,
                              durability=DurabilityPolicy.VOLATILE)
        node.create_subscription(LiftRequest, "lift_requests", self.on_request, transient)
        self.state_pub = node.create_publisher(LiftState, "lift_states", volatile)
        node.create_timer(0.5, self.publish_states)
        self.client = make_client(f"rmf-lift-adapter-{os.getpid()}")
        if username:
            self.client.username_pw_set(username, password)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=10)
        self.client.connect_async(host, port, keepalive=30)
        self.client.loop_start()
        node.get_logger().info(f"Lift adapter: /lift_requests <-> mqtt://{host}:{port}/{self.prefix}/+/")

    def on_connect(self, client, userdata, flags, rc, properties=None):
        client.subscribe(f"{self.prefix}/+/state", qos=1)

    def on_message(self, client, userdata, message):
        try:
            data = json.loads(message.payload)
            name = message.topic[len(self.prefix) + 1:-len("/state")]
            if not isinstance(data, dict) or data.get("lift_name", name) != name:
                raise ValueError("lift_name does not match the topic")
        except (ValueError, UnicodeDecodeError) as error:
            self.node.get_logger().warning(f"Ignored lift state on {message.topic}: {error}")
            return
        with self.lock:
            self.states[name] = (data, self.clock())

    def on_request(self, msg):
        if not msg.lift_name or any(c in msg.lift_name for c in "/+#"):
            self.node.get_logger().warning(f"Ignored lift request with invalid lift_name {msg.lift_name!r}")
            return
        self.client.publish(f"{self.prefix}/{msg.lift_name}/request",
                            json.dumps(request_to_json(msg)), qos=1)

    def publish_states(self):
        now = self.clock()
        stamp = self.node.get_clock().now().to_msg()
        with self.lock:
            states = list(self.states.values())
        for data, received in states:
            self.state_pub.publish(fill_state(
                self.LiftState(), data, stamp, offline=now - received > self.stale_after))

    def close(self):
        self.client.loop_stop()
        self.client.disconnect()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--mqtt_host", default=os.environ.get("MQTT_BROKER_HOST", "127.0.0.1"))
    parser.add_argument("--mqtt_port", type=int, default=int(os.environ.get("MQTT_BROKER_PORT", 1883)))
    parser.add_argument("--mqtt_username", default=os.environ.get("MQTT_USERNAME"))
    parser.add_argument("--mqtt_password", default=os.environ.get("MQTT_PASSWORD"))
    parser.add_argument("--topic_prefix", default="rmf/lift")
    parser.add_argument("--stale_after", type=float, default=3.0)
    args = parser.parse_args()

    import rclpy

    rclpy.init()
    node = rclpy.create_node("mqtt_lift_adapter")
    bridge = LiftBridge(node, args.mqtt_host, args.mqtt_port, args.topic_prefix,
                        args.mqtt_username, args.mqtt_password, args.stale_after)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        bridge.close()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
