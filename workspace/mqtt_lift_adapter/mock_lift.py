#!/usr/bin/env python3
"""MQTT lift simulator with the same protocol as the Isaac Sim elevator (--elevator-mqtt-lift).

Use it with mock_robot.py to test Open-RMF multi-floor tasks without Isaac Sim:
    python3 mock_lift.py --lift lift_1 --floors 1F,B1
"""

import argparse
import json
import os
import threading
import time

import paho.mqtt.client as mqtt

DOOR_SECONDS = 2.0
TRAVEL_SECONDS = 6.0


class MockLift:
    """Same session and door rules as Isaac's LiftController, with timed doors and travel."""

    def __init__(self, name, floors, clock=time.monotonic):
        self.name, self.floors, self.clock = name, list(floors), clock
        self.current = self.destination = self.floors[0]
        self.session_id = ""
        self.door_request = "closed"
        self.door = 0.0  # 0 closed .. 1 open
        self.travel_end = None
        self.last = clock()
        self.lock = threading.Lock()

    def request(self, data):
        kind = data.get("request_type")
        session = str(data.get("session_id", ""))
        with self.lock:
            if kind == "end_session":
                if session == self.session_id:
                    self.session_id = ""
                return
            if kind not in ("agv", "human") or data.get("destination_floor") not in self.floors:
                raise ValueError(f"invalid lift request {data}")
            if self.session_id and session != self.session_id:
                return
            if kind == "agv":
                self.session_id = session
            self.destination = data["destination_floor"]
            self.door_request = data.get("door_state", "open")

    def update(self):
        now = self.clock()
        with self.lock:
            dt, self.last = now - self.last, now
            if self.travel_end is not None:
                if now >= self.travel_end:
                    self.current, self.travel_end = self.destination, None
                return
            if self.destination != self.current:
                self.door = max(0.0, self.door - dt / DOOR_SECONDS)
                if self.door == 0.0:
                    self.travel_end = now + TRAVEL_SECONDS
                return
            step = dt / DOOR_SECONDS
            self.door = min(1.0, self.door + step) if self.door_request == "open" else max(0.0, self.door - step)

    def state(self):
        with self.lock:
            moving = self.travel_end is not None
            going_up = self.floors.index(self.destination) < self.floors.index(self.current)
            return {
                "lift_name": self.name,
                "available_floors": self.floors,
                "current_floor": self.current,
                "destination_floor": self.destination,
                "door_state": "closed" if self.door <= 0.0 else "open" if self.door >= 1.0 else "moving",
                "motion_state": ("up" if going_up else "down") if moving else "stopped",
                "current_mode": "agv" if self.session_id else "human",
                "session_id": self.session_id,
            }


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--lift", default="lift_1")
    parser.add_argument("--floors", default="1F,B1", help="Floor names, top to bottom.")
    parser.add_argument("--mqtt_host", default=os.environ.get("MQTT_BROKER_HOST", "127.0.0.1"))
    parser.add_argument("--mqtt_port", type=int, default=int(os.environ.get("MQTT_BROKER_PORT", 1883)))
    parser.add_argument("--topic_prefix", default="rmf/lift")
    args = parser.parse_args()

    lift = MockLift(args.lift, [f.strip() for f in args.floors.split(",") if f.strip()])
    base = f"{args.topic_prefix.strip('/')}/{args.lift}"
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=f"mock-lift-{os.getpid()}")
    except AttributeError:
        client = mqtt.Client(client_id=f"mock-lift-{os.getpid()}")
    client.will_set(f"{base}/state", json.dumps(dict(lift.state(), current_mode="offline")), qos=1, retain=True)

    def on_message(client, userdata, message):
        try:
            lift.request(json.loads(message.payload))
        except ValueError as error:
            print(f"[mock_lift] rejected: {error}", flush=True)

    client.on_connect = lambda c, u, f, rc, p=None: c.subscribe(f"{base}/request", qos=1)
    client.on_message = on_message
    client.connect(args.mqtt_host, args.mqtt_port, keepalive=30)
    client.loop_start()
    print(f"[mock_lift] {args.lift} floors={lift.floors} on mqtt://{args.mqtt_host}:{args.mqtt_port}/{base}", flush=True)
    last = None
    try:
        while True:
            lift.update()
            state = lift.state()
            client.publish(f"{base}/state", json.dumps(state), qos=0, retain=True)
            if state != last:
                print(f"[mock_lift] {state}", flush=True)
                last = state
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        client.publish(f"{base}/state", json.dumps(dict(lift.state(), current_mode="offline")),
                       qos=1, retain=True).wait_for_publish()
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
